# jobs/pub_date_transition.py
"""
pub_date_transition.py

Identifies every product whose effective_pub_date has passed but whose stored
status is not yet settled, and forces reclassification against current Shopify
state.

Why it is needed: classification depends on date.today() at the moment it runs,
and nothing re-evaluates a product just because the calendar moved. A product
is only reclassified by a Shopify webhook, a manual /reclassify call, or this
sweep.

Scope: every status EXCEPT the two settled ones (historical_preorder,
not_a_preorder_product). Anomaly statuses are included deliberately — an
anomaly row whose pub date later passes is never revisited otherwise, and some
anomalies can only hold while the date is in the future
(anomaly_missing_collection requires effective_pub_date >= today;
anomaly_override_conflict Case 3 requires pub_date > today). Those rows are
leftovers that a fresh classification clears. Before this change the sweep
selected only active_preorder and early_stock_arrival, so they were stranded.

Safe to run multiple times — reclassification is idempotent.
Products already transitioned are no-ops.

Intended schedule: daily via Railway cron. (A weekly run left a title stuck for
up to six days; the schedule itself lives in Railway, not in this repo.)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List

from dependencies import get_supabase_client, get_shopify_client
from shopify_client import ShopifyClient

UTC = timezone.utc
logger = logging.getLogger(__name__)


#: Statuses that need no date-driven re-evaluation. Everything else is swept.
SETTLED_STATUSES = ["historical_preorder", "not_a_preorder_product"]


async def fetch_stale_preorders(supabase, limit: int) -> List[int]:
    """
    Fetch products whose effective_pub_date has passed (or is today) and whose
    stored status is not settled — including anomaly statuses, which nothing
    else re-evaluates once the date moves.
    """
    today = date.today().isoformat()

    resp = (
        supabase
        .schema("preorder")
        .table("product_status")
        .select("product_id")
        .not_.in_("status", SETTLED_STATUSES)
        .lte("effective_pub_date", today)
        .limit(limit)
        .execute()
    )

    return [int(row["product_id"]) for row in resp.data or []]


async def run(limit: int = 200, dry_run: bool = False) -> Dict[str, Any]:
    supabase = get_supabase_client()
    shopify_client = ShopifyClient()

    product_ids = await fetch_stale_preorders(supabase, limit=limit)

    if not product_ids:
        logger.info("[pub_date_transition] No stale preorders found")
        await shopify_client.close()
        return {
            "stale_found": 0,
            "reclassified": 0,
            "errors": [],
            "dry_run": dry_run,
            "ran_at_utc": datetime.now(UTC).isoformat(),
        }

    logger.info(f"[pub_date_transition] Found {len(product_ids)} stale preorders")

    reclassified = 0
    errors = []

    if not dry_run:
        from services.reclassification_service import reclassify_single_product

        for pid in product_ids:
            try:
                await reclassify_single_product(
                    supabase=supabase,
                    shopify_client=shopify_client,
                    product_id=pid,
                )
                reclassified += 1
                logger.info(f"[pub_date_transition] Reclassified {pid}")
            except Exception as e:
                errors.append({"product_id": pid, "error": str(e)})
                logger.error(f"[pub_date_transition] Failed {pid}: {e}")

    await shopify_client.close()

    summary = {
        "stale_found": len(product_ids),
        "reclassified": reclassified,
        "errors": errors,
        "dry_run": dry_run,
        "ran_at_utc": datetime.now(UTC).isoformat(),
    }

    logger.info("[pub_date_transition] completed", extra=summary)
    return summary