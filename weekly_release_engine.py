#!/usr/bin/env python3
"""
weekly_release_engine.py

Produces a Sunday->Saturday merged reporting CSV for preorder releases.

For each eligible release candidate:
    Weekly Sales = banked_presales + in_week_sales

Also marks released products in preorder.release_state so they are not
double-counted on future runs.
"""

from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Dict, Iterable, List, Optional

import requests
from supabase import create_client, Client

from dotenv import load_dotenv
load_dotenv()


ET = ZoneInfo("America/New_York")
ENGINE_VERSION = "weekly-release-engine-v1"


@dataclass
class ReleaseCandidate:
    product_id: int
    effective_pub_date: date
    classification_status: str
    lifecycle_state: Optional[str]
    arrival_timing: Optional[str]


@dataclass
class ProductMetadata:
    product_id: int
    isbn: str
    title: str
    author: str
    publisher: str


@dataclass
class ReleaseRow:
    product_id: int
    isbn: str
    title: str
    author: str
    publisher: str
    banked_presales: int
    in_week_sales: int

    @property
    def weekly_sales(self) -> int:
        return self.banked_presales + self.in_week_sales


def get_supabase() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def get_shopify_headers() -> Dict[str, str]:
    shop = os.environ["SHOP_URL"]
    token = os.environ["SHOPIFY_ACCESS_TOKEN"]
    api_version = os.environ.get("API_VERSION", "2025-10")
    endpoint = f"https://{shop}/admin/api/{api_version}/graphql.json"
    return {
        "endpoint": endpoint,
        "headers": {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": token,
        },
    }


def resolve_target_week(target_date: Optional[str]) -> tuple[date, date]:
    """
    Returns Sunday->Saturday ET week boundaries as dates.
    """
    if target_date:
        anchor = date.fromisoformat(target_date)
    else:
        # Default behavior: report always runs AFTER a week has closed.
        # Therefore anchor to the prior week so Monday runs report
        # for the previous Sunday→Saturday window.
        anchor = datetime.now(ET).date() - timedelta(days=7)

    # Python Monday=0 ... Sunday=6
    days_since_sunday = (anchor.weekday() + 1) % 7
    week_start = anchor - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def fetch_release_candidates(supabase: Client, week_start: date, week_end: date) -> List[ReleaseCandidate]:
    resp = (
        supabase.schema("preorder")
        .table("vw_candidate_release_base")
        .select("product_id,effective_pub_date,classification_status,lifecycle_state,arrival_timing,already_reported")
        .gte("effective_pub_date", str(week_start))
        .lte("effective_pub_date", str(week_end))
        .eq("already_reported", False)
        .execute()
    )

    candidates: List[ReleaseCandidate] = []
    for row in resp.data or []:
        candidates.append(
            ReleaseCandidate(
                product_id=int(row["product_id"]),
                effective_pub_date=date.fromisoformat(row["effective_pub_date"]),
                classification_status=row["classification_status"],
                lifecycle_state=row.get("lifecycle_state"),
                arrival_timing=row.get("arrival_timing"),
            )
        )
    return candidates


def fetch_banked_presales(supabase: Client, candidates: List[ReleaseCandidate]) -> Dict[int, int]:
    """
    Computes presale_sales_total from commitment_ledger using ET pub-date boundary.
    """
    if not candidates:
        return {}

    candidate_map = {c.product_id: c for c in candidates}
    product_ids = list(candidate_map.keys())

    # Pull ledger rows for candidate products and compute locally.
    # This avoids requiring a custom RPC just to get started.
    resp = (
        supabase.schema("preorder")
        .table("commitment_ledger")
        .select("product_id,topic,delta_qty,occurred_at")
        .in_("product_id", product_ids)
        .execute()
    )

    totals = {pid: 0 for pid in product_ids}

    for row in resp.data or []:
        pid = int(row["product_id"])
        topic = row["topic"]
        delta = int(row["delta_qty"])
        occurred_at = datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))

        pub_boundary_et = datetime.combine(candidate_map[pid].effective_pub_date, datetime.min.time(), tzinfo=ET)
        pub_boundary_utc = pub_boundary_et.astimezone(ZoneInfo("UTC"))

        if occurred_at >= pub_boundary_utc:
            continue

        if topic in ("orders/create", "orders/create_backfill", "orders/paid"):
            totals[pid] += delta
        elif topic in ("orders/cancelled", "refunds/create"):
            totals[pid] += delta

    return totals


def fetch_product_metadata(shopify_product_ids: List[int]) -> Dict[int, ProductMetadata]:
    """
    Replace this with your preferred product metadata fetcher.
    This version uses Shopify GraphQL product IDs directly.
    """
    if not shopify_product_ids:
        return {}

    cfg = get_shopify_headers()
    endpoint = cfg["endpoint"]
    headers = cfg["headers"]

    metadata: Dict[int, ProductMetadata] = {}

    for product_id in shopify_product_ids:
        query = """
        query Product($id: ID!) {
          product(id: $id) {
            legacyResourceId
            title
            vendor
            metafield(namespace: "custom", key: "author") { value }
            variants(first: 5) {
              edges {
                node {
                  barcode
                }
              }
            }
          }
        }
        """
        gid = f"gid://shopify/Product/{product_id}"
        r = requests.post(
            endpoint,
            headers=headers,
            json={"query": query, "variables": {"id": gid}},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()["data"]["product"]
        if not data:
            continue

        barcode = ""
        edges = data.get("variants", {}).get("edges", [])
        if edges:
            barcode = edges[0]["node"].get("barcode") or ""

        metadata[product_id] = ProductMetadata(
            product_id=product_id,
            isbn=barcode,
            title=data.get("title") or "",
            author=(data.get("metafield") or {}).get("value") or "",
            publisher=data.get("vendor") or "",
        )

    return metadata


def fetch_in_week_sales(week_start: date, week_end: date) -> Dict[int, int]:
    """
    Pulls Sunday->Saturday sales directly from Shopify orders for the target week.
    """
    cfg = get_shopify_headers()
    endpoint = cfg["endpoint"]
    headers = cfg["headers"]

    week_start_et = datetime.combine(week_start, datetime.min.time(), tzinfo=ET)
    week_end_exclusive_et = datetime.combine(week_end + timedelta(days=1), datetime.min.time(), tzinfo=ET)
    start_utc = week_start_et.astimezone(ZoneInfo("UTC")).isoformat()
    end_utc = week_end_exclusive_et.astimezone(ZoneInfo("UTC")).isoformat()

    query = """
    query Orders($cursor: String, $query: String!) {
      orders(first: 250, after: $cursor, query: $query, sortKey: CREATED_AT) {
        pageInfo {
          hasNextPage
          endCursor
        }
        edges {
          node {
            createdAt
            lineItems(first: 250) {
              edges {
                node {
                  quantity
                  currentQuantity
                  product { legacyResourceId }
                }
              }
            }
          }
        }
      }
    }
    """

    # Pull a slightly wider window and normalize timestamps in Python
    # because Shopify stores createdAt in UTC and Sunday-night ET
    # orders can appear as Monday UTC.
    shopify_query = f"created_at:>={start_utc} created_at:<{end_utc}"
    sales: Dict[int, int] = {}
    cursor = None

    while True:
        r = requests.post(
            endpoint,
            headers=headers,
            json={"query": query, "variables": {"cursor": cursor, "query": shopify_query}},
            timeout=60,
        )
        r.raise_for_status()
        payload = r.json()["data"]["orders"]

        for edge in payload["edges"]:
            order = edge["node"]

            # Normalize Shopify UTC timestamp → ET
            created_at_utc = datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00"))
            created_at_et = created_at_utc.astimezone(ET)

            # Enforce Sunday→Saturday window in ET
            if created_at_et < week_start_et or created_at_et >= week_end_exclusive_et:
                continue

            for line_edge in order["lineItems"]["edges"]:
                node = line_edge["node"]
                product = node.get("product")
                if not product:
                    continue

                pid = product.get("legacyResourceId")
                if not pid:
                    continue

                # Use currentQuantity (net after refunds/cancellations) if available.
                qty = node.get("currentQuantity")
                if qty is None:
                    qty = node.get("quantity", 0)

                sales[pid] = sales.get(pid, 0) + int(qty)

        if not payload["pageInfo"]["hasNextPage"]:
            break
        cursor = payload["pageInfo"]["endCursor"]

    return sales


def build_release_rows(
    candidates: List[ReleaseCandidate],
    presales: Dict[int, int],
    week_sales: Dict[int, int],
    metadata: Dict[int, ProductMetadata],
) -> List[ReleaseRow]:
    rows: List[ReleaseRow] = []

    for candidate in candidates:
        meta = metadata.get(candidate.product_id)
        if not meta:
            continue

        rows.append(
            ReleaseRow(
                product_id=candidate.product_id,
                isbn=meta.isbn,
                title=meta.title,
                author=meta.author,
                publisher=meta.publisher,
                banked_presales=presales.get(candidate.product_id, 0),
                in_week_sales=week_sales.get(candidate.product_id, 0),
            )
        )

    rows.sort(key=lambda r: (r.publisher.lower(), r.title.lower()))
    return rows


def write_csv(rows: List[ReleaseRow], week_end: date, output_dir: str) -> str:
    filename = f"nyt_release_report_{week_end.isoformat()}.csv"
    path = os.path.join(output_dir, filename)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ISBN", "QTY"])
        for row in rows:
            writer.writerow([
                row.isbn,
                row.weekly_sales,
            ])

    return path


def mark_reported(
    supabase: Client,
    candidates: List[ReleaseCandidate],
    week_start: date,
    week_end: date,
    csv_filename: str,
) -> None:
    payload = []
    now_utc = datetime.utcnow().isoformat() + "Z"

    for c in candidates:
        payload.append({
            "product_id": c.product_id,
            "effective_pub_date": str(c.effective_pub_date),
            "released_to_reporting": True,
            "release_report_week_start": str(week_start),
            "release_report_week_end": str(week_end),
            "released_at": now_utc,
            "engine_version": ENGINE_VERSION,
            "csv_filename": csv_filename,
            "updated_at": now_utc,
        })

    if payload:
        supabase.schema("preorder").table("release_state").upsert(
            payload,
            on_conflict="product_id,effective_pub_date"
        ).execute()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", help="Any ISO date inside the target Sunday->Saturday week")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mark-reported", action="store_true")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    week_start, week_end = resolve_target_week(args.week)
    # For deterministic reruns we compare against the report week end,
    # not the current day the script is executed.
    report_cutoff_date = week_end
    supabase = get_supabase()

    candidates = fetch_release_candidates(supabase, week_start, week_end)
    # Only release titles whose pub date has actually occurred
    candidates = [c for c in candidates if c.effective_pub_date <= report_cutoff_date]

    presales = fetch_banked_presales(supabase, candidates)
    week_sales = fetch_in_week_sales(week_start, week_end)

    # Combine all products that sold this week with preorder releases
    all_product_ids = set(week_sales.keys()) | set(presales.keys())

    # Fetch preorder status for all involved products so we can suppress
    # preorder titles whose pub date is still in the future.
    status_resp = (
        supabase.schema("preorder")
        .table("product_status")
        .select("product_id,status,effective_pub_date")
        .in_("product_id", list(all_product_ids))
        .execute()
    )

    # Build lookup of product -> (status, pub_date)
    status_lookup: dict[int, tuple[str | None, date | None]] = {}

    for row in status_resp.data or []:
        pid = int(row["product_id"])
        status = row.get("status")
        pub = row.get("effective_pub_date")

        pub_date = date.fromisoformat(pub) if pub else None
        status_lookup[pid] = (status, pub_date)

    if not all_product_ids:
        print(f"No sales or preorder releases for week {week_start} -> {week_end}")
        return

    metadata = fetch_product_metadata(list(all_product_ids))

    rows: List[ReleaseRow] = []
    for pid in all_product_ids:
        status, pub_date = status_lookup.get(pid, (None, None))

        # If this product is a preorder and its pub date has not yet occurred,
        # it must never appear in the report.
        if status in ("active_preorder", "early_stock_arrival"):
            if pub_date is None or pub_date > report_cutoff_date:
                continue

        meta = metadata.get(pid)
        if not meta:
            continue

        isbn = (meta.isbn or "").strip()

        # NYT reporting rule: only include traditional 13‑digit ISBNs starting with 978 or 979
        if len(isbn) != 13 or not (isbn.startswith("978") or isbn.startswith("979")):
            continue

        rows.append(
            ReleaseRow(
                product_id=pid,
                isbn=isbn,
                title=meta.title,
                author=meta.author,
                publisher=meta.publisher,
                banked_presales=presales.get(pid, 0),
                in_week_sales=week_sales.get(pid, 0),
            )
        )

    csv_path = write_csv(rows, week_end, args.output_dir)

    print(f"Wrote {len(rows)} rows to {csv_path}")

    if args.mark_reported and not args.dry_run:
        mark_reported(
            supabase=supabase,
            candidates=candidates,
            week_start=week_start,
            week_end=week_end,
            csv_filename=os.path.basename(csv_path),
        )
        print("Release state updated.")


if __name__ == "__main__":
    main()