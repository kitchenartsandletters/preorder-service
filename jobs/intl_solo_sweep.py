"""
jobs/intl_solo_sweep.py

Scheduled backstop for international cart isolation ("intl-solo").

Reclassification already restricts new preorders as their products/update
webhook is processed (services/intl_solo_actions.auto_restrict). This sweep
catches anything a missed or failed webhook left behind:

  - restricts preorders with no decision yet (pending_restriction)
  - re-adds the intl-solo tag to restricted titles that lost it

It never clears a title or removes a tag. Those, and stray tags with no
decision behind them, are listed under "needs_person" in the summary for
someone to handle in the dashboard.

Run:  python jobs/run.py --job intl_solo_sweep [--dry-run]
Kill switch: INTL_SOLO_AUTO_TAG=off
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from dependencies import get_supabase_client
from services import intl_solo_actions
from shopify_client import ShopifyClient

logger = logging.getLogger(__name__)


async def run(dry_run: bool = False) -> Dict[str, Any]:
    supabase = get_supabase_client()
    client = ShopifyClient()
    try:
        out = await intl_solo_actions.sweep(supabase, client, dry_run=dry_run)
    finally:
        await client.close()

    summary = {
        "dry_run": dry_run,
        "disabled": out.get("disabled", False),
        "count": out.get("count", 0),
        "counts": out.get("counts", {}),
        "failed": out.get("failed", 0),
        "needs_person": out.get("needs_person", []),
        "ran_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if summary["needs_person"]:
        logger.warning(f"[intl_solo_sweep] {len(summary['needs_person'])} item(s) need a person")
    logger.info(f"[intl_solo_sweep] completed: {summary['counts']}")
    return summary
