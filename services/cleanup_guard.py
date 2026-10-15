"""
services/cleanup_guard.py

Refuses cleanup actions that would end a *delayed-import hold*.

A delayed import is a title whose pub date has passed but whose stock has never
physically arrived, and which still has open preorder commitments. The engine
deliberately keeps it `active_preorder` (see `classification.engine._is_delayed_import`):
customers have ordered and are waiting, so the title is legitimately still a
preorder.

Removing such a title from the Preorder collection flips it straight to
`historical_preorder` — the hold ends while the stock is still in transit. The
cleanup routes act on Shopify state alone and never consulted
`preorder.product_status` or `preorder.inventory_arrival`, so nothing stopped
that. The admin-dashboard hides the Full Cleanup button for held titles; this
guard is the API-side backstop for a direct call or a stale browser tab.

Detection mirrors the engine's own rule, read from the DB rather than recomputed:
- stored status is `active_preorder`, AND
- `effective_pub_date` is in the past (pub day itself is not a hold — being
  active on pub day is normal), AND
- no `inventory_arrival` row exists.

A past-dated `active_preorder` can only have come from the delayed-import branch:
the standard active path requires `effective_pub_date >= today`. The arrival check
is therefore belt-and-braces, and it keeps the rule self-documenting.

NOTE ON TIME ZONES: `date.today()` here is the container's local date, matching
`classification.engine`. The rest of the system (lifecycle snapshotter, reporting
week bounds) uses ET explicitly. That inconsistency is recorded in
docs/DOCS_STATUS.md; this module deliberately matches the engine so the guard and
the classification agree with each other.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

HELD_STATUS = "active_preorder"


def _as_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def check_delayed_import_hold(supabase, product_id: int) -> Optional[Dict[str, Any]]:
    """
    Return hold details when `product_id` is a delayed-import hold, else None.

    Returns None when the product is unknown to product_status, so the guard
    never blocks cleanup on something it has no information about.
    """
    try:
        resp = (
            supabase.schema("preorder")
            .table("product_status")
            .select("product_id, status, effective_pub_date")
            .eq("product_id", product_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None

        row = rows[0]
        if row.get("status") != HELD_STATUS:
            return None

        effective_pub_date = _as_date(row.get("effective_pub_date"))
        if effective_pub_date is None or effective_pub_date >= date.today():
            return None

        arrival = (
            supabase.schema("preorder")
            .table("inventory_arrival")
            .select("product_id")
            .eq("product_id", product_id)
            .limit(1)
            .execute()
        )
        if arrival.data:
            return None

        return {
            "reason": "delayed_import_hold",
            "product_id": product_id,
            "status": row.get("status"),
            "effective_pub_date": effective_pub_date.isoformat(),
            "message": (
                "This title is a delayed import: its pub date has passed but stock has "
                "not arrived, and it is deliberately still an active preorder. Cleaning "
                "it up would end that hold and reclassify it as historical."
            ),
            "resolution": (
                "Wait for stock to arrive (an inventory_arrival record ends the hold "
                "automatically), or repeat the request with force=true to override."
            ),
        }
    except Exception as exc:  # pragma: no cover - defensive
        # A guard that cannot read state must not silently allow the action.
        logger.error(f"[cleanup_guard] hold check failed for {product_id}: {exc}")
        raise
