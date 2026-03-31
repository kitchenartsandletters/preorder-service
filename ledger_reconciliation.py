#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone, date
from typing import Any, Dict, List, Optional

import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
UTC = timezone.utc

@dataclass(frozen=True)
class ReconciliationRow:
    product_id: int
    effective_pub_date: Optional[date]
    ledger_open_qty: int
    shopify_open_qty: int
    delta: int


class ShopifyGraphQLError(Exception):
    pass


class ShopifyClient:
    def __init__(self) -> None:
        self.shop_url = os.environ["SHOP_URL"]
        self.access_token = os.environ["SHOPIFY_ACCESS_TOKEN"]
        self.api_version = os.environ.get("API_VERSION", "2025-10")
        self.endpoint = f"https://{self.shop_url}/admin/api/{self.api_version}/graphql.json"
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=20.0),
            headers={
                "X-Shopify-Access-Token": self.access_token,
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def graphql(self, query: str, variables: dict | None = None):
        MAX_RETRIES = 5

        for attempt in range(MAX_RETRIES):
            resp = await self.client.post(
                self.endpoint,
                json={"query": query, "variables": variables or {}},
            )

            data = resp.json()

            # --- SUCCESS ---
            if "errors" not in data:
                return data["data"]

            # --- THROTTLED ---
            if any(e.get("extensions", {}).get("code") == "THROTTLED" for e in data["errors"]):
                wait_time = min(2 ** attempt, 10)  # cap backoff
                logger.warning(f"[THROTTLED] retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue

            # --- OTHER ERROR ---
            raise ShopifyGraphQLError(data["errors"])

        raise Exception("Exceeded max Shopify retries")


OPEN_ORDER_COMMITMENTS_QUERY = """
query OpenOrderCommitmentsForProduct($cursor: String, $query: String!) {
  orders(
    first: 100
    after: $cursor
    sortKey: CREATED_AT
    reverse: true
    query: $query
  ) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        name
        createdAt
        cancelledAt
        displayFinancialStatus
        displayFulfillmentStatus
        refunds {
          id
        }
        lineItems(first: 100) {
          edges {
            node {
              id
              currentQuantity
              quantity
              unfulfilledQuantity
              refundableQuantity
              product {
                id
                legacyResourceId
              }
              variant {
                id
                legacyResourceId
              }
            }
          }
        }
      }
    }
  }
}
"""


async def get_pool() -> asyncpg.Pool:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required")
    return await asyncpg.create_pool(dsn=dsn, ssl="require")


async def fetch_snapshot_products(pool: asyncpg.Pool, limit: int = 500) -> List[Dict[str, Any]]:
    rows = await pool.fetch(
        """
        select distinct cl.product_id, ps.effective_pub_date
        from preorder.commitment_ledger cl
        join preorder.product_status ps
          on ps.product_id = cl.product_id
        where ps.status in ('active_preorder', 'historical_preorder')
        order by ps.effective_pub_date asc nulls last, cl.product_id asc
        limit $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def fetch_ledger_open_qty(pool: asyncpg.Pool, product_id: int) -> int:
    row = await pool.fetchrow(
        """
        select coalesce(sum(cl.delta_qty), 0) as total
        from preorder.commitment_ledger cl
        join preorder.product_status ps
          on ps.product_id = cl.product_id
        where cl.product_id = $1
          and ps.status in ('active_preorder', 'historical_preorder')
          and cl.topic in ('orders/create', 'orders/fulfilled', 'refunds/create')
          and cl.topic not in ('orders/create_backfill', 'reconciliation.adjustment')
        """,
        product_id,
    )
    total = int(row["total"] if row else 0)
    return max(total, 0)


async def fetch_shopify_open_qty(shopify: ShopifyClient, product_id: int, max_pages: int = 5) -> int:
    MAX_RETRIES_PER_PAGE = 3

    cursor = None
    pages = 0
    total_open = 0

    while True:
        attempt = 0
        while True:
            try:
                data = await shopify.graphql(
                    OPEN_ORDER_COMMITMENTS_QUERY,
                    {"cursor": cursor, "query": f"line_items.product_id:{product_id} status:open"}
                )
                break
            except Exception as e:
                attempt += 1
                if attempt >= MAX_RETRIES_PER_PAGE:
                    logger.error("Shopify fetch failed after retries", extra={"product_id": product_id})
                    return total_open  # fail soft, keep partial
                wait_time = min(2 ** attempt, 10)
                logger.warning(f"[RETRY] product_id={product_id} retrying in {wait_time}s due to {e}")
                await asyncio.sleep(wait_time)

        orders = data["orders"]
        for edge in orders["edges"]:
            order = edge["node"]

            if order.get("cancelledAt"):
                continue

            for li_edge in order["lineItems"]["edges"]:
                li = li_edge["node"]
                product = li.get("product")
                if not product:
                    continue

                if int(product["legacyResourceId"]) != product_id:
                    continue

                unfulfilled = li.get("unfulfilledQuantity") or 0
                if unfulfilled and unfulfilled > 0:
                    total_open += int(unfulfilled)

        if not orders["pageInfo"]["hasNextPage"]:
            break

        cursor = orders["pageInfo"]["endCursor"]
        pages += 1
        if pages >= max_pages:
            logger.warning(
                "Reached max_pages while fetching Shopify commitments",
                extra={"product_id": product_id, "partial_total_open": total_open},
            )
            break

    return total_open


async def insert_reconciliation_log(
    pool: asyncpg.Pool,
    *,
    product_id: int,
    effective_pub_date: Optional[date],
    ledger_open_qty: int,
    shopify_open_qty: int,
    delta: int,
    action: str,
    dry_run: bool,
    note: Optional[str] = None,
) -> None:
    await pool.execute(
        """
        insert into preorder.reconciliation_log (
          product_id,
          effective_pub_date,
          ledger_open_qty,
          shopify_open_qty,
          delta,
          action,
          dry_run,
          note,
          created_at
        )
        values ($1, $2, $3, $4, $5, $6, $7, $8, now())
        """,
        product_id,
        effective_pub_date,
        ledger_open_qty,
        shopify_open_qty,
        delta,
        action,
        dry_run,
        note,
    )


async def insert_adjustment(
    pool: asyncpg.Pool,
    *,
    product_id: int,
    delta: int,
) -> None:
    topic = "reconciliation.adjustment"

    await pool.execute(
        """
        insert into preorder.commitment_ledger (
          id,
          tracking_id,
          event_id,
          topic,
          product_id,
          variant_id,
          order_id,
          line_item_id,
          delta_qty,
          occurred_at,
          created_at,
          fulfillment_id,
          refund_id
        )
        values (
          gen_random_uuid(),
          gen_random_uuid(),
          gen_random_uuid(),
          $1,
          $2,
          null,
          null,
          null,
          $3,
          now(),
          now(),
          null,
          null
        )
        """,
        topic,
        product_id,
        delta,
    )


async def reconcile_product(
    pool: asyncpg.Pool,
    shopify: ShopifyClient,
    *,
    product_id: int,
    effective_pub_date: Optional[date],
    dry_run: bool,
) -> ReconciliationRow:
    ledger_open_qty = await fetch_ledger_open_qty(pool, product_id)
    shopify_open_qty = await fetch_shopify_open_qty(shopify, product_id)
    delta = shopify_open_qty - ledger_open_qty

    action = "noop"
    note = None

    if delta != 0:
        action = "would_adjust" if dry_run else "adjusted"
        note = "positive delta means missing create-like qty; negative delta means missing fulfillment/refund-like qty"

        if not dry_run:
            await insert_adjustment(pool, product_id=product_id, delta=delta)

    await insert_reconciliation_log(
        pool,
        product_id=product_id,
        effective_pub_date=effective_pub_date,
        ledger_open_qty=ledger_open_qty,
        shopify_open_qty=shopify_open_qty,
        delta=delta,
        action=action,
        dry_run=dry_run,
        note=note,
    )

    return ReconciliationRow(
        product_id=product_id,
        effective_pub_date=effective_pub_date,
        ledger_open_qty=ledger_open_qty,
        shopify_open_qty=shopify_open_qty,
        delta=delta,
    )


async def run(limit: int, dry_run: bool, product_id: Optional[int]) -> Dict[str, Any]:
    pool = await get_pool()
    shopify = ShopifyClient()

    try:
        if product_id is not None:
            products = [{"product_id": product_id, "effective_pub_date": None}]
        else:
            products = await fetch_snapshot_products(pool, limit=limit)

        # deterministic chunking guard
        products = sorted(products, key=lambda x: (x.get("effective_pub_date") or date.min, x["product_id"]))

        results: List[ReconciliationRow] = []

        BATCH_SIZE = 10
        DELAY_BETWEEN_BATCHES = 2.0

        for i in range(0, len(products), BATCH_SIZE):
            batch = products[i:i + BATCH_SIZE]

            for p in batch:
                row = await reconcile_product(
                    pool,
                    shopify,
                    product_id=int(p["product_id"]),
                    effective_pub_date=p.get("effective_pub_date"),
                    dry_run=dry_run,
                )
                results.append(row)

            # throttle between batches
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

        mismatches = [r for r in results if r.delta != 0]

        return {
            "ok": True,
            "checked": len(results),
            "mismatches": len(mismatches),
            "dry_run": dry_run,
            "results": [r.__dict__ for r in mismatches[:100]],
            "ran_at_utc": datetime.now(UTC).isoformat(),
        }

    finally:
        await shopify.close()
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--product-id", type=int, default=None)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    summary = asyncio.run(
        run(
            limit=args.limit,
            dry_run=not args.write,
            product_id=args.product_id,
        )
    )

    print(summary)


if __name__ == "__main__":
    main()