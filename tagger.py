#!/usr/bin/env python3
"""
Order Tagger — Workstream 2 of preorder-service consolidation
Replaces: NYT_weekly_and_preorder_release / preorderOrderTagger.py
           + refreshPreorderProductIDs.py (now reads Supabase directly)

Runs as a Railway cron job.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx
from supabase import create_client, Client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SHOPIFY_STORE        = os.environ["SHOPIFY_URL"]           # store.myshopify.com
SHOPIFY_ACCESS_TOKEN = os.environ["SHOPIFY_ACCESS_TOKEN"]
SUPABASE_URL         = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2025-01")
GRAPHQL_URL         = f"https://{SHOPIFY_STORE}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

TAG_PREORDER   = "preorder"
TAG_MIXED      = "mixed"
TAGGER_VERSION = "1.0.0"
LOOKBACK_DAYS  = 30
PAGE_SIZE      = 50    # orders per page (Shopify max 250; 50 is safe with nested line items)

# ── GraphQL documents ─────────────────────────────────────────────────────────
ORDERS_QUERY = """
query FetchOrders($query: String!, $first: Int!, $after: String) {
  orders(query: $query, first: $first, after: $after, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      tags
      lineItems(first: 50) {
        nodes {
          product { id }
          customAttributes { key value }
        }
      }
    }
  }
}
"""

ORDER_UPDATE_MUTATION = """
mutation OrderUpdate($input: OrderInput!) {
  orderUpdate(input: $input) {
    order { id tags }
    userErrors { field message }
  }
}
"""


# ── Shopify helpers ───────────────────────────────────────────────────────────
async def shopify_gql(client: httpx.AsyncClient, query: str, variables: dict) -> dict:
    resp = await client.post(
        GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers={
            "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
            "Content-Type": "application/json",
        },
    )
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    return body["data"]


async def tag_order(
    client: httpx.AsyncClient,
    order_gid: str,
    existing_tags: list[str],
    new_tags: list[str],
) -> list[str]:
    """Merge new_tags into existing_tags and apply via orderUpdate."""
    merged = list(set(existing_tags) | set(new_tags))
    data = await shopify_gql(
        client, ORDER_UPDATE_MUTATION, {"input": {"id": order_gid, "tags": merged}}
    )
    user_errors = data["orderUpdate"]["userErrors"]
    if user_errors:
        raise RuntimeError(f"orderUpdate userErrors: {user_errors}")
    return data["orderUpdate"]["order"]["tags"]


# ── Supabase helpers ──────────────────────────────────────────────────────────
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def get_preorder_product_gids(sb: Client) -> set[str]:
    """
    Pull active preorder product IDs from preorder.product_status.
    Replaces refreshPreorderProductIDs.py entirely — no Storefront API call needed.
    """
    rows = (
        sb.schema("preorder")
        .from_("product_status")
        .select("product_id")
        .in_("status", ["active_preorder", "early_stock_arrival"])
        .execute()
    )
    # product_id is a numeric Shopify ID; build Shopify GID for comparison with line items
    return {f"gid://shopify/Product/{r['product_id']}" for r in rows.data}


def get_already_processed_gids(sb: Client) -> set[str]:
    """Return all order GIDs already recorded in tagger_processed_orders."""
    rows = (
        sb.schema("preorder")
        .from_("tagger_processed_orders")
        .select("order_gid")
        .execute()
    )
    return {r["order_gid"] for r in rows.data}


def create_run(sb: Client) -> str:
    """Open a tagger_run_log row in 'running' state. Returns the run UUID."""
    result = (
        sb.schema("preorder")
        .from_("tagger_run_log")
        .insert({"status": "running", "tagger_version": TAGGER_VERSION})
        .execute()
    )
    return result.data[0]["id"]


def finish_run(sb: Client, run_id: str, stats: dict, errors: list, success: bool):
    tagged = stats["orders_tagged"]
    if not success and tagged == 0:
        status = "error"
    elif errors:
        status = "partial"
    else:
        status = "success"

    sb.schema("preorder").from_("tagger_run_log").update(
        {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "orders_fetched": stats["orders_fetched"],
            "orders_skipped": stats["orders_skipped"],
            "orders_tagged": tagged,
            "preorder_count": stats["preorder_count"],
            "mixed_count": stats["mixed_count"],
            "errors": errors,
        }
    ).eq("id", run_id).execute()


def record_processed_order(
    sb: Client,
    order_gid: str,
    order_name: str,
    tags: list[str],
    run_id: str,
):
    sb.schema("preorder").from_("tagger_processed_orders").insert(
        {
            "order_gid": order_gid,
            "order_name": order_name,
            "tags_applied": tags,
            "run_id": run_id,
        }
    ).execute()


# ── Classification logic ──────────────────────────────────────────────────────
def classify_order(order: dict, preorder_gids: set[str]) -> tuple[bool, bool]:
    """
    Returns (has_preorder, is_mixed).

    has_preorder — at least one line item is a preorder product, either by
                   product GID match or the _preorder custom attribute safety net.
    is_mixed     — order contains both preorder and non-preorder line items.
    """
    has_preorder_item = False
    has_regular_item  = False

    for item in order["lineItems"]["nodes"]:
        product     = item.get("product") or {}
        product_gid = product.get("id")
        custom      = {a["key"]: a["value"] for a in item.get("customAttributes", [])}

        is_preorder = (product_gid in preorder_gids) or (custom.get("_preorder") == "true")

        if is_preorder:
            has_preorder_item = True
        else:
            has_regular_item = True

    return has_preorder_item, (has_preorder_item and has_regular_item)


# ── Main entry point ──────────────────────────────────────────────────────────
async def run_tagger():
    sb     = get_supabase()
    run_id = create_run(sb)
    log.info(f"Tagger run started — run_id={run_id} version={TAGGER_VERSION}")

    stats: dict = {
        "orders_fetched": 0,
        "orders_skipped": 0,
        "orders_tagged":  0,
        "preorder_count": 0,
        "mixed_count":    0,
    }
    errors: list[dict] = []

    try:
        preorder_gids   = get_preorder_product_gids(sb)
        processed_gids  = get_already_processed_gids(sb)
        log.info(
            f"Loaded {len(preorder_gids)} preorder product GIDs, "
            f"{len(processed_gids)} already-processed orders"
        )

        since = (
            datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        order_query = f"created_at:>={since} status:open"

        async with httpx.AsyncClient(timeout=30.0) as client:
            cursor = None
            while True:
                variables: dict = {"query": order_query, "first": PAGE_SIZE}
                if cursor:
                    variables["after"] = cursor

                data   = await shopify_gql(client, ORDERS_QUERY, variables)
                page   = data["orders"]
                orders = page["nodes"]
                stats["orders_fetched"] += len(orders)
                log.info(f"Fetched page of {len(orders)} orders")

                for order in orders:
                    order_gid  = order["id"]
                    order_name = order["name"]

                    # Already processed in a prior run — skip without re-recording
                    if order_gid in processed_gids:
                        stats["orders_skipped"] += 1
                        continue

                    has_preorder, is_mixed = classify_order(order, preorder_gids)

                    if not has_preorder:
                        # Not a preorder order — don't record; it might gain preorder
                        # line items via edits on a future run
                        continue

                    new_tags = [TAG_PREORDER]
                    if is_mixed:
                        new_tags.append(TAG_MIXED)

                    try:
                        await tag_order(client, order_gid, order["tags"], new_tags)
                        record_processed_order(sb, order_gid, order_name, new_tags, run_id)
                        processed_gids.add(order_gid)  # prevent double-processing in same run
                        stats["orders_tagged"] += 1
                        if is_mixed:
                            stats["mixed_count"] += 1
                        else:
                            stats["preorder_count"] += 1
                        log.info(f"Tagged {order_name} ({order_gid}) → {new_tags}")
                    except Exception as exc:
                        errors.append({
                            "order_gid":  order_gid,
                            "order_name": order_name,
                            "error":      str(exc),
                        })
                        log.error(f"Failed to tag {order_name}: {exc}")

                if not page["pageInfo"]["hasNextPage"]:
                    break
                cursor = page["pageInfo"]["endCursor"]

    except Exception as exc:
        log.error(f"Fatal tagger error: {exc}", exc_info=True)
        errors.append({"error": str(exc), "fatal": True})
        finish_run(sb, run_id, stats, errors, success=False)
        sys.exit(1)

    finish_run(sb, run_id, stats, errors, success=True)
    log.info(
        f"Run complete — "
        f"fetched={stats['orders_fetched']} "
        f"skipped={stats['orders_skipped']} "
        f"tagged={stats['orders_tagged']} "
        f"(preorder={stats['preorder_count']} mixed={stats['mixed_count']}) "
        f"errors={len(errors)}"
    )


if __name__ == "__main__":
    asyncio.run(run_tagger())