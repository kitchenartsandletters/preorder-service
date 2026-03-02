#!/usr/bin/env python3
"""
Phase 12.5 — Step 3.5
Seed preorder.product_status baseline rows from Shopify
for products appearing in commitment_ledger.

Scope:
- Ledger products only
- Compute effective_pub_date
- Record anomaly_type
- Do NOT compute lifecycle state
"""

import asyncio
import json
import re
from datetime import datetime, date
from typing import Optional, Tuple, List

import asyncpg

from db.connection import get_pool
from shopify_client import ShopifyClient


ENGINE_VERSION = "phase_12_5_baseline"


DATE_TAG_PATTERN = re.compile(r"^\d{2}-\d{2}-\d{4}$")


# ----------------------------------------------------
# Helpers
# ----------------------------------------------------

def parse_yyyy_mm_dd(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def parse_mm_dd_yyyy(value: str) -> Optional[date]:
    try:
        return datetime.strptime(value, "%m-%d-%Y").date()
    except Exception:
        return None


def resolve_effective_pub_date(product: dict) -> Tuple[Optional[date], Optional[str]]:
    """
    Returns (effective_pub_date, anomaly_type)
    """

    metafields = product.get("metafields", {}).get("edges", [])
    tags = product.get("tags", [])

    # 1️⃣ Check custom.pub_date metafield
    for edge in metafields:
        node = edge.get("node", {})
        if node.get("namespace") == "custom" and node.get("key") == "pub_date":
            value = node.get("value")
            d = parse_yyyy_mm_dd(value)
            if d:
                # Sanity guard: reject implausible legacy placeholder dates
                if d.year < 2000:
                    return None, "implausible_pub_date"
                return d, None
            return None, "invalid_pub_date_format"

    # 2️⃣ Fallback to single date tag
    date_tags = [t for t in tags if DATE_TAG_PATTERN.match(t)]

    if len(date_tags) == 1:
        d = parse_mm_dd_yyyy(date_tags[0])
        if d:
            # Sanity guard: reject implausible legacy placeholder dates
            if d.year < 2000:
                return None, "implausible_pub_date"
            return d, None
        return None, "invalid_pub_date_format"

    if len(date_tags) > 1:
        return None, "multiple_date_tags"

    return None, "missing_pub_date"


# ----------------------------------------------------
# Main
# ----------------------------------------------------

UPSERT_SQL = """
insert into preorder.product_status
(
  product_id,
  status,
  anomaly_type,
  effective_pub_date,
  last_classified_at,
  metadata_snapshot,
  engine_version
)
values
($1, $2, $3, $4, now(), $5, $6)
on conflict (product_id)
do update set
  status = excluded.status,
  anomaly_type = excluded.anomaly_type,
  effective_pub_date = excluded.effective_pub_date,
  last_classified_at = now(),
  metadata_snapshot = excluded.metadata_snapshot,
  engine_version = excluded.engine_version;
"""


async def fetch_ledger_product_ids(pool) -> List[int]:
    rows = await pool.fetch("""
        select distinct product_id
        from preorder.commitment_ledger
        order by product_id
    """)
    return [r["product_id"] for r in rows]


async def process_product(pool, shopify, product_id: int):
    max_retries = 5
    backoff = 1.0

    attempt = 0

    while True:
        attempt += 1
        try:
            product = await shopify.fetch_product_full(product_id)
            break
        except Exception as e:
            message = str(e)

            # Explicit handling for Shopify throttling
            if "THROTTLED" in message or "Throttled" in message:
                if attempt >= max_retries:
                    print(f"Max retries reached (throttled) for product {product_id}")
                    return

                print(
                    f"Throttled on product {product_id}, retry {attempt}/{max_retries} "
                    f"— sleeping {backoff:.1f}s"
                )
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            # Non-throttle error
            print(f"Failed to fetch product {product_id}: {e}")
            return

    effective_pub_date, anomaly = resolve_effective_pub_date(product)

    await pool.execute(
        UPSERT_SQL,
        product_id,
        "baseline",
        anomaly,
        effective_pub_date,
        json.dumps(product),
        ENGINE_VERSION,
    )


async def main():
    pool = await get_pool()
    shopify = ShopifyClient()

    product_ids = await fetch_ledger_product_ids(pool)

    print(f"Seeding {len(product_ids)} products...")

    # Reduce concurrency to avoid Shopify GraphQL throttling
    sem = asyncio.Semaphore(3)

    async def bounded(pid):
        async with sem:
            await process_product(pool, shopify, pid)

    await asyncio.gather(*(bounded(pid) for pid in product_ids))

    await shopify.close()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())