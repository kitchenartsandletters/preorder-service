"""
services/hold_service.py

Explicit, expiring preorder holds (preorder.preorder_hold).

A hold is a deliberate decision to keep a past-dated title an active preorder,
and therefore in the Preorder collection, which is what drives the rewards
system and the preorder copy. Two reasons:

  event           a signing or similar; the incentive runs through the date
  awaiting_stock  stock genuinely in transit; keep preorder presentation

This replaces the engine's old inference ("past pub date AND no stock arrival"
meant held, indefinitely). See db/migrations/2026_10_create_preorder_hold.sql.

TIME ZONE: `hold_until` is the LAST held day, inclusive, evaluated in
America/New_York — the same rule the dashboard uses and the same expression
`vw_preorder_products.hold_active` applies in SQL. Everything that needs to know
whether a hold is live asks this module or that column; nothing re-derives it.
The classifier's own `date.today()` is the container's date (likely UTC), which
is why expiry is NOT evaluated with it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

TABLE = "preorder_hold"
SCHEMA = "preorder"

VALID_REASONS = ("event", "awaiting_stock")


def today_et() -> date:
    """Today's date in Eastern — the clock holds are measured against."""
    return datetime.now(ET).date()


def _as_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def is_active(hold: Optional[Dict[str, Any]], as_of: Optional[date] = None) -> bool:
    """True when `hold` exists and its last held day has not passed (ET)."""
    if not hold:
        return False
    hold_until = _as_date(hold.get("hold_until"))
    if hold_until is None:
        return False
    return hold_until >= (as_of or today_et())


def get_hold(supabase, product_id: int) -> Optional[Dict[str, Any]]:
    """The product's hold row, expired or not, or None."""
    resp = (
        supabase.schema(SCHEMA)
        .table(TABLE)
        .select("product_id, hold_until, reason, note, created_by, created_at, updated_at")
        .eq("product_id", product_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


def has_active_hold(supabase, product_id: int) -> bool:
    """True when the product is under a live hold. The engine's input."""
    return is_active(get_hold(supabase, product_id))


def set_hold(
    supabase,
    product_id: int,
    hold_until: date,
    reason: str,
    note: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create or replace the hold on a product. One hold per product.

    Raises ValueError for an unknown reason or a hold that has already expired —
    setting a hold in the past would be a no-op that looks like it worked.
    """
    if reason not in VALID_REASONS:
        raise ValueError(f"reason must be one of {VALID_REASONS}, got {reason!r}")

    hold_until = _as_date(hold_until)
    if hold_until is None:
        raise ValueError("hold_until must be a date (YYYY-MM-DD)")
    if hold_until < today_et():
        raise ValueError(
            f"hold_until {hold_until.isoformat()} is already past "
            f"(today is {today_et().isoformat()} ET); the hold would have no effect"
        )

    payload = {
        "product_id": product_id,
        "hold_until": hold_until.isoformat(),
        "reason": reason,
        "note": note,
        "created_by": created_by,
        "updated_at": datetime.now(ET).isoformat(),
    }
    supabase.schema(SCHEMA).table(TABLE).upsert(payload, on_conflict="product_id").execute()
    logger.info(
        f"[hold] set product_id={product_id} until={hold_until} reason={reason} by={created_by}"
    )
    return {**payload, "hold_active": True}


def clear_hold(supabase, product_id: int) -> bool:
    """Remove the hold. Returns True when one was removed."""
    existing = get_hold(supabase, product_id)
    if not existing:
        return False
    supabase.schema(SCHEMA).table(TABLE).delete().eq("product_id", product_id).execute()
    logger.info(f"[hold] cleared product_id={product_id}")
    return True


def list_holds(supabase, include_expired: bool = False) -> List[Dict[str, Any]]:
    """
    Holds, newest expiry first, each annotated with `hold_active`.

    include_expired=True also returns lapsed holds, which is what a
    "recently expired" alert needs — an expired hold stops suppressing cleanup
    immediately, so it would otherwise vanish from view.
    """
    resp = (
        supabase.schema(SCHEMA)
        .table(TABLE)
        .select("product_id, hold_until, reason, note, created_by, created_at, updated_at")
        .order("hold_until", desc=True)
        .execute()
    )
    as_of = today_et()
    holds = []
    for row in resp.data or []:
        active = is_active(row, as_of=as_of)
        if active or include_expired:
            holds.append({**row, "hold_active": active})
    return holds
