"""
Admin Pub-Date Routes (rev2 Move 1c)
====================================
The sanctioned way to change a preorder's publication date.

  PUT /admin/preorders/{product_id}/pub-date       — set custom.pub_date
  GET /admin/preorders/{product_id}/pub-date-history — audit trail

Order of operations for a write, and why:

  1. Read the current value and its compare-and-set digest.
  2. Record a write intent (preorder.pubdate_write_intent).
  3. metafieldsSet with the digest.
  4. Reclassify synchronously from fresh Shopify state.

Step 2 happens BEFORE step 3 because Shopify sends products/update for our own
edit. Without an intent waiting, the orchestrator would label the dashboard's
own change `shopify_direct_edit` — reporting an out-of-band edit that never
happened. With it, the row reads `ui_change` and carries who changed it and why.
Whichever path commits first, the other finds no diff and writes nothing.

The metafield TYPE is never hard-coded: it is read from the live metafield, or
from its definition when the product has no value yet (services/pubdate_service).

Auth: X-Admin-Token, matching the other admin_* routers. Uses the lazy Supabase
client rather than creating one at import (docs/DOCS_STATUS.md Landmine 10).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from dependencies import get_supabase_client, get_shopify_client
from services import pubdate_service
from services.pubdate_service import PubDateConflict, PubDateError
from services.reclassification_service import reclassify_single_product

logger = logging.getLogger(__name__)

router = APIRouter()

ADMIN_TOKEN = os.getenv("PREORDER_ADMIN_TOKEN")


def require_admin_token(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True


class PubDateRequest(BaseModel):
    pub_date: date = Field(..., description="New publication date (YYYY-MM-DD)")
    reason: Optional[str] = Field(None, description="Why it changed; stored in history")
    changed_by: Optional[str] = Field(None, description="Who changed it")


@router.get("/{product_id}/pub-date-history")
def get_pub_date_history(
    product_id: int,
    limit: int = 50,
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
):
    """
    History for one product, newest first.

    `reconstructed` rows (tag_history_backfill) carry no real timestamp — the
    tags they came from had none — so they are flagged rather than presented as
    dated events. `override_migration` rows record the one-off fold of the
    retired override into pub_date, not a change of effective date.
    """
    rows = (
        supabase.schema("preorder")
        .table("pubdate_history")
        .select("old_effective_pub_date, new_effective_pub_date, change_source, "
                "engine_version, changed_at, metadata")
        .eq("product_id", product_id)
        .order("changed_at", desc=True)
        .limit(limit)
        .execute()
    ).data or []

    for row in rows:
        metadata = row.get("metadata") or {}
        row["reconstructed"] = bool(metadata.get("reconstructed"))
        row["is_fold"] = row.get("change_source") == "override_migration"
        row["changed_by"] = metadata.get("changed_by")
        row["reason"] = metadata.get("reason")

    return {"product_id": product_id, "count": len(rows), "history": rows}


@router.put("/{product_id}/pub-date")
async def set_pub_date(
    product_id: int,
    payload: PubDateRequest,
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
    shopify_client=Depends(get_shopify_client),
):
    """
    Set custom.pub_date, then reclassify from fresh Shopify state.

    409 when the value changed in Shopify since it was read (compare-and-set),
    so an operator refreshes rather than silently overwriting someone.
    """
    try:
        state = await pubdate_service.read_pub_date_state(shopify_client, product_id)
    except PubDateError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["value"] == payload.pub_date.isoformat():
        return {
            "product_id": product_id,
            "result": "no_change",
            "pub_date": state["value"],
            "message": "pub_date already has this value.",
        }

    intent = (
        supabase.schema("preorder")
        .table("pubdate_write_intent")
        .insert({
            "product_id": product_id,
            "new_date": payload.pub_date.isoformat(),
            "changed_by": payload.changed_by,
            "reason": payload.reason,
        })
        .execute()
    )
    intent_id = ((intent.data or [{}])[0]).get("id")

    try:
        await pubdate_service.set_pub_date(
            shopify_client,
            product_id=product_id,
            new_pub_date=payload.pub_date,
            metafield_type=state["metafield_type"],
            compare_digest=state["compare_digest"],
        )
    except (PubDateConflict, PubDateError) as exc:
        # The write did not happen, so the intent must not sit open waiting to
        # mislabel an unrelated later change as ui_change.
        _close_intent(supabase, intent_id, product_id, payload.pub_date)
        status = 409 if isinstance(exc, PubDateConflict) else 502
        raise HTTPException(status_code=status, detail=str(exc))

    result = await reclassify_single_product(
        supabase=supabase, shopify_client=shopify_client, product_id=product_id
    )

    history = (
        supabase.schema("preorder")
        .table("pubdate_history")
        .select("old_effective_pub_date, new_effective_pub_date, change_source, changed_at, metadata")
        .eq("product_id", product_id)
        .order("changed_at", desc=True)
        .limit(1)
        .execute()
    ).data or []

    return {
        "product_id": product_id,
        "result": "updated",
        "previous_pub_date": state["value"],
        "pub_date": payload.pub_date.isoformat(),
        "classification": getattr(result, "status", None) or (result or {}).get("status"),
        "history_row": history[0] if history else None,
        "description_warning": (
            "The product description may still state the old date: the bulk "
            "importer writes a release-date sentence into the body HTML, which "
            "this endpoint does not touch."
        ),
    }


def _close_intent(supabase, intent_id, product_id: int = None, new_date: date = None) -> None:
    """
    Mark an intent consumed after a failed write.

    Falls back to (product_id, new_date) when the insert did not return an id:
    an intent left open would be claimed by the next unrelated change to that
    date and mislabel it ui_change, which is worse than closing one row too
    many — the fallback only ever matches intents for this exact date.
    """
    try:
        query = supabase.schema("preorder").table("pubdate_write_intent").update(
            {"consumed_at": datetime.now(timezone.utc).isoformat()}
        )
        if intent_id:
            query = query.eq("id", intent_id)
        elif product_id is not None and new_date is not None:
            query = query.eq("product_id", product_id).eq("new_date", new_date.isoformat())
        else:
            return
        query.execute()
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning(f"[pubdate] could not close intent {intent_id}: {exc}")
