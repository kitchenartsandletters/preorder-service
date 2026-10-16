"""
Admin Hold Routes
=================
Set, list and clear explicit preorder holds (preorder.preorder_hold).

A hold keeps a past-dated title an active preorder — and therefore in the
Preorder collection, which is what drives the rewards system and the preorder
copy. It replaces the engine's old inference. See services/hold_service.py and
db/migrations/2026_10_create_preorder_hold.sql.

Endpoints (mounted under /admin/preorders):
  GET    /holds                      — list holds (?include_expired=true)
  GET    /holds/{product_id}         — one hold, with hold_active
  PUT    /holds/{product_id}         — create or replace a hold
  DELETE /holds/{product_id}         — clear a hold

Auth: X-Admin-Token, matching the other admin_* routers.

Note: these routes use the lazy Supabase client rather than creating one at
import time (docs/DOCS_STATUS.md Landmine 10).
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from dependencies import get_supabase_client
from services import hold_service

logger = logging.getLogger(__name__)

router = APIRouter()

ADMIN_TOKEN = os.getenv("PREORDER_ADMIN_TOKEN")


def require_admin_token(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True


class HoldRequest(BaseModel):
    hold_until: date = Field(..., description="Last held day, inclusive, in ET")
    reason: str = Field(..., description="event | awaiting_stock")
    note: Optional[str] = Field(None, description='Free text, e.g. "signing with the author"')
    created_by: Optional[str] = Field(None, description="Who set it")


@router.get("/holds")
def list_holds(
    include_expired: bool = False,
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
):
    holds = hold_service.list_holds(supabase, include_expired=include_expired)
    return {
        "today_et": hold_service.today_et().isoformat(),
        "include_expired": include_expired,
        "count": len(holds),
        "holds": holds,
    }


@router.get("/holds/{product_id}")
def get_hold(
    product_id: int,
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
):
    hold = hold_service.get_hold(supabase, product_id)
    if not hold:
        raise HTTPException(status_code=404, detail=f"No hold for product {product_id}")
    return {**hold, "hold_active": hold_service.is_active(hold)}


@router.put("/holds/{product_id}")
def put_hold(
    product_id: int,
    payload: HoldRequest,
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
):
    """
    Create or replace the hold on a product.

    400 for an unknown reason or a hold_until that has already passed in ET:
    such a hold would be inert, which is worse than being refused.
    """
    try:
        return hold_service.set_hold(
            supabase,
            product_id=product_id,
            hold_until=payload.hold_until,
            reason=payload.reason,
            note=payload.note,
            created_by=payload.created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/holds/{product_id}")
def delete_hold(
    product_id: int,
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
):
    removed = hold_service.clear_hold(supabase, product_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No hold for product {product_id}")
    return {"product_id": product_id, "cleared": True}
