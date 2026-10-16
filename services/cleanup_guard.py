"""
services/cleanup_guard.py

Refuses cleanup actions that would end an explicit preorder hold.

A hold (preorder.preorder_hold) is a deliberate decision to keep a past-dated
title an active preorder: a signing (`event`) or stock genuinely in transit
(`awaiting_stock`). What is being held is Preorder collection membership, which
drives the rewards system and the preorder copy — so removing the title from the
collection ends the hold early, before the event or before the stock lands.

This guard previously INFERRED a hold from (status active_preorder AND past pub
date AND no inventory arrival). Owner decision 2026-10-15 retired that
inference, so the guard now reads the hold record. A past-dated title with no
hold is cleaned up freely, which is the common case.

The admin-dashboard hides the cleanup controls for held titles; this guard is
the API-side backstop for a direct call or a stale browser tab. Expiry is
evaluated in ET by services/hold_service.py, the same rule the dashboard and the
SQL view use, so the two cannot disagree.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from services import hold_service

logger = logging.getLogger(__name__)


def check_active_hold(supabase, product_id: int) -> Optional[Dict[str, Any]]:
    """
    Return hold details when `product_id` is under a live hold, else None.

    Reads preorder.preorder_hold. Nothing is inferred: a title is held only
    because someone set a hold with an end date.
    """
    hold = hold_service.get_hold(supabase, product_id)
    if not hold_service.is_active(hold):
        return None

    reason = hold.get("reason")
    note = hold.get("note")
    return {
        "reason": "preorder_hold",
        "product_id": product_id,
        "hold_until": str(hold.get("hold_until")),
        "hold_reason": reason,
        "note": note,
        "message": (
            f"This title is held until {hold.get('hold_until')} "
            f"({note or reason}). The hold keeps it in the Preorder collection, "
            "which is what keeps the preorder incentive and copy live; cleaning it "
            "up would end that early."
        ),
        "resolution": (
            "Wait for the hold to expire, clear it via "
            "DELETE /admin/preorders/holds/{product_id}, or repeat this request "
            "with force=true to override."
        ),
    }
