#!/usr/bin/env python3
"""
Phase 13.5a — Populate preorder.inventory_item_map

Full catalog mapping:
inventory_item_id -> variant_id -> product_id

Usage:
  python populate_inventory_item_map.py
  python populate_inventory_item_map.py --dry-run
  python populate_inventory_item_map.py --limit-products 500
"""

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple

from db.connection import get_pool
from shopify_client import ShopifyClient

logger = logging.getLogger(__name__)

ENGINE_VERSION = "v13.5-inventory-item-map"


def _gid_to_int(gid: Optional[str]) -> Optional[int]:
    """
    gid://shopify/Product/123 -> 123
    """
    if not gid or not isinstance(gid, str):
        return None
    try:
        return int(gid.rsplit("/", 1)[-1])
    except Exception:
        return None


QUERY_PRODUCTS_WITH_VARIANTS = """
query ProductsWithVariants($cursor: String) {
  products(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        variants(first: 100) {
          edges {
            node {
              id
              inventoryItem { id }
            }
          }
        }
      }
    }
  }
}
"""


UPSERT_SQL = """
insert into preorder.inventory_item_map (inventory_item_id, variant_id, product_id, updated_at)
values ($1::bigint, $2::bigint, $3::bigint, now())
on conflict (inventory_item_id) do update
set variant_id = excluded.variant_id,
    product_id = excluded.product_id,
    updated_at = now()
"""


async def upsert_rows(pool, rows: List[Tuple[int, int, int]], dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        return len(rows)

    # executemany is much faster than row-by-row
    await pool.executemany(UPSERT_SQL, rows)
    return len(rows)


async def run(dry_run: bool = False, limit_products: Optional[int] = None) -> Dict[str, Any]:
    pool = await get_pool()
    shop = ShopifyClient()

    cursor = None
    total_products = 0
    total_rows = 0

    try:
        while True:
            data = await shop.graphql(QUERY_PRODUCTS_WITH_VARIANTS, {"cursor": cursor})
            block = data.get("products") or {}
            edges = block.get("edges") or []
            page_info = block.get("pageInfo") or {}

            if not edges:
                break

            batch_rows: List[Tuple[int, int, int]] = []

            for e in edges:
                node = (e or {}).get("node") or {}
                product_id = _gid_to_int(node.get("id"))
                if not product_id:
                    continue

                total_products += 1

                v_edges = ((node.get("variants") or {}).get("edges")) or []
                for ve in v_edges:
                    v = (ve or {}).get("node") or {}
                    variant_id = _gid_to_int(v.get("id"))
                    inv_item_id = _gid_to_int(((v.get("inventoryItem") or {}).get("id")))
                    if not variant_id or not inv_item_id:
                        continue
                    batch_rows.append((inv_item_id, variant_id, product_id))

                if limit_products and total_products >= limit_products:
                    # stop after we processed requested number of products
                    page_info["hasNextPage"] = False
                    break

            total_rows += await upsert_rows(pool, batch_rows, dry_run=dry_run)

            if not page_info.get("hasNextPage"):
                break

            cursor = page_info.get("endCursor")

        return {
            "dry_run": dry_run,
            "engine_version": ENGINE_VERSION,
            "products_scanned": total_products,
            "rows_upserted": total_rows,
        }

    finally:
        await shop.close()


def main():
    logging.basicConfig(level=logging.INFO)

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit-products", type=int, default=None)
    args = ap.parse_args()

    summary = asyncio.run(run(dry_run=args.dry_run, limit_products=args.limit_products))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()