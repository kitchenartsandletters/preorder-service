# jobs/pub_date_transition.py
"""
pub_date_transition.py

Identifies active_preorder and early_stock_arrival products whose
effective_pub_date has passed and forces reclassification against
current Shopify state.

Ensures products transition to historical_preorder on pub date
without waiting for a natural Shopify webhook event.

Safe to run multiple times — reclassification is idempotent.
Products already transitioned are no-ops.

Intended schedule: Tuesdays 11:00 UTC (07:00 ET) via Railway cron.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List

from dependencies import get_supabase_client, get_shopify_client
from shopify_client import ShopifyClient

UTC = timezone.utc
logger = logging.getLogger(__name__)


async def fetch_stale_preorders(supabase, limit: int) -> List[int]:
    """
    Fetch active_preorder and early_stock_arrival products whose
    effective_pub_date has passed but have not yet transitioned
    to historical_preorder.
    """
    today = date.today().isoformat()

    resp = (
        supabase
        .schema("preorder")
        .table("product_status")
        .select("product_id")
        .in_("status", ["active_preorder", "early_stock_arrival"])
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