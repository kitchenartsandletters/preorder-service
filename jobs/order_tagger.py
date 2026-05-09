"""
jobs/order_tagger.py

Order Tagging job — Workstream 2 of preorder-service consolidation.

Replaces: NYT_weekly_and_preorder_release / preorderOrderTagger.py
          + refreshPreorderProductIDs.py (reads Supabase directly instead)

Entry point: called by jobs/run.py --job order_tagger
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import httpx
from supabase import create_client, Client

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SHOPIFY_STORE        = os.environ["SHOPIFY_STORE"]
SHOPIFY_ACCESS_TOKEN = os.environ["SHOPIFY_ACCESS_TOKEN"]
SUPABASE_URL         = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2025-01")
GRAPHQL_URL         = f"https://{SHOPIFY_STORE}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

TAG_PREORDER   = "preorder"
TAG_MIXED      = "mixed"
TAGGER_VERSION = "1.0.0"
LOOKBACK_DAYS  = 30
PAGE_SIZE      = 50

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
async def _shopify_gql(client: httpx.AsyncClient, query: str, variables: dict) -> dict:
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


async def _tag_order(
    client: httpx.AsyncClient,
    order_gid: str,
    existing_tags: list[str],
    new_tags: list[str],
) -> list[str]:
    merged = list(set(existing_tags) | set(new_tags))
    data = await _shopify_gql(
        client, ORDER_UPDATE_MUTATION, {"input": {"id": order_gid, "tags": merged}}
    )
    user_errors = data["orderUpdate"]["userErrors"]
    if user_errors:
        raise RuntimeError(f"orderUpdate userErrors: {user_errors}")
    return data["orderUpdate"]["order"]["tags"]


# ── Supabase helpers ──────────────────────────────────────────────────────────
def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _get_preorder_product_gids(sb: Client) -> set[str]:
    """
    Replaces refreshPreorderProductIDs.py — reads directly from Supabase
    instead of querying the Shopify Storefront API.
    """
    rows = (
        sb.schema("preorder")
        .from_("product_status")
        .select("product_id")
        .in_("status", ["active_preorder", "early_stock_arrival"])
        .execute()
    )
    return {f"gid://shopify/Product/{r['product_id']}" for r in rows.data}


def _get_processed_gids(sb: Client) -> set[str]:
    rows = (
        sb.schema("preorder")
        .from_("tagger_processed_orders")
        .select("order_gid")
        .execute()
    )
    return {r["order_gid"] for r in rows.data}


def _create_run(sb: Client) -> str:
    result = (
        sb.schema("preorder")
        .from_("tagger_run_log")
        .insert({"status": "running", "tagger_version": TAGGER_VERSION})
        .execute()
    )
    return result.data[0]["id"]


def _finish_run(sb: Client, run_id: str, stats: dict, errors: list, success: bool):
    tagged = stats["orders_tagged"]
    if not success and tagged == 0:
        status = "error"
    elif errors:
        status = "partial"
    else:
        status = "success"

    sb.schema("preorder").from_("tagger_run_log").update({
        "completed_at":  datetime.now(timezone.utc).isoformat(),
        "status":        status,
        "orders_fetched": stats["orders_fetched"],
        "orders_skipped": stats["orders_skipped"],
        "orders_tagged":  tagged,
        "preorder_count": stats["preorder_count"],
        "mixed_count":    stats["mixed_count"],
        "errors":         errors,
    }).eq("id", run_id).execute()


def _record_processed_order(
    sb: Client, order_gid: str, order_name: str, tags: list[str], run_id: str
):
    sb.schema("preorder").from_("tagger_processed_orders").insert({
        "order_gid":   order_gid,
        "order_name":  order_name,
        "tags_applied": tags,
        "run_id":      run_id,
    }).execute()


# ── Classification logic ──────────────────────────────────────────────────────
def _classify_order(order: dict, preorder_gids: set[str]) -> tuple[bool, bool]:
    """
    Returns (has_preorder, is_mixed).
    Mirrors original preorderOrderTagger.py logic exactly:
    - Product GID match against active preorder set
    - _preorder custom attribute as safety net
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


# ── Public entry point (called by jobs/run.py) ────────────────────────────────
async def run(limit: int = 2000, dry_run: bool = False) -> Dict[str, Any]:
    """
    Main tagger entry point. `limit` is unused (pagination is unbounded
    within the 30-day lookback window) but accepted for dispatcher compat.
    """
    sb     = _get_supabase()
    run_id = _create_run(sb)
    log.info(f"Order tagger started — run_id={run_id} dry_run={dry_run}")

    stats: dict = {
        "orders_fetched": 0,
        "orders_skipped": 0,
        "orders_tagged":  0,
        "preorder_count": 0,
        "mixed_count":    0,
    }
    errors: list[dict] = []

    try:
        preorder_gids  = _get_preorder_product_gids(sb)
        processed_gids = _get_processed_gids(sb)
        log.info(
            f"Loaded {len(preorder_gids)} preorder product GIDs, "
            f"{len(processed_gids)} already-processed order GIDs"
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

                data   = await _shopify_gql(client, ORDERS_QUERY, variables)
                page   = data["orders"]
                orders = page["nodes"]
                stats["orders_fetched"] += len(orders)
                log.info(f"Fetched page of {len(orders)} orders")

                for order in orders:
                    order_gid  = order["id"]
                    order_name = order["name"]

                    if order_gid in processed_gids:
                        stats["orders_skipped"] += 1
                        continue

                    has_preorder, is_mixed = _classify_order(order, preorder_gids)

                    if not has_preorder:
                        continue

                    new_tags = [TAG_PREORDER]
                    if is_mixed:
                        new_tags.append(TAG_MIXED)

                    try:
                        if not dry_run:
                            await _tag_order(client, order_gid, order["tags"], new_tags)
                            _record_processed_order(sb, order_gid, order_name, new_tags, run_id)

                        processed_gids.add(order_gid)
                        stats["orders_tagged"] += 1
                        if is_mixed:
                            stats["mixed_count"] += 1
                        else:
                            stats["preorder_count"] += 1
                        log.info(
                            f"{'[dry_run] Would tag' if dry_run else 'Tagged'} "
                            f"{order_name} ({order_gid}) → {new_tags}"
                        )
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
        _finish_run(sb, run_id, stats, errors, success=False)
        raise  # let run.py catch and set exit code 1

    if not dry_run:
        _finish_run(sb, run_id, stats, errors, success=True)

    log.info(
        f"Run complete — "
        f"fetched={stats['orders_fetched']} "
        f"skipped={stats['orders_skipped']} "
        f"tagged={stats['orders_tagged']} "
        f"(preorder={stats['preorder_count']} mixed={stats['mixed_count']}) "
        f"errors={len(errors)}"
    )

    return {
        "run_id":         run_id,
        "dry_run":        dry_run,
        "preorder_gids":  len(preorder_gids),
        **stats,
        "error_count":    len(errors),
        "errors":         errors,
    }