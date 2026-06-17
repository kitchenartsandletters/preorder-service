#!/usr/bin/env python3
"""
weekly_release_engine.py

Produces the Sunday->Saturday merged reporting CSV used for weekly reporting.

Rules:
- The report ALWAYS includes regular weekly sales for qualifying books.
- Preorder presales are included ONLY for titles explicitly queued in
  preorder.release_state for the target reporting week.
- The engine does NOT derive release candidates from pub dates on its own.
- Future preorder titles must never leak into the report.
- CSV output format is exactly: ISBN,QTY
"""

from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
from supabase import Client, create_client
from dotenv import load_dotenv

from shopify_token import get_token_sync

# Load environment variables from .env in project root
load_dotenv()


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
ENGINE_VERSION = "weekly-release-engine-v2"


@dataclass(frozen=True)
class ReleaseCandidate:
    product_id: int
    effective_pub_date: date


@dataclass(frozen=True)
class ProductMetadata:
    product_id: int
    isbn: str
    title: str
    author: str
    publisher: str


@dataclass(frozen=True)
class ReleaseRow:
    product_id: int
    isbn: str
    qty: int


def get_supabase() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def get_shopify_config() -> Dict[str, object]:
    shop = os.environ["SHOP_URL"]
    from shopify_token import get_token_sync
    token = get_token_sync()
    api_version = os.environ.get("SHOPIFY_API_VERSION", "2025-10")
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

    Default behavior on Monday is to report the PRIOR completed week.
    """
    if target_date:
        anchor = date.fromisoformat(target_date)
    else:
        anchor = datetime.now(ET).date() - timedelta(days=7)

    days_since_sunday = (anchor.weekday() + 1) % 7
    week_start = anchor - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def fetch_admin_releases(supabase: Client, week_start: date, week_end: date) -> List[ReleaseCandidate]:
    """
    Fetch preorder titles explicitly queued for release by the admin layer.

    The weekly engine trusts preorder.release_state as reporting authority.
    It must not derive release candidates on its own from pub dates.
    """
    resp = (
        supabase.schema("preorder")
        .table("release_state")
        .select("product_id,effective_pub_date,released_to_reporting,release_report_week_start,release_report_week_end")
        .eq("released_to_reporting", False)
        .eq("release_report_week_start", str(week_start))
        .eq("release_report_week_end", str(week_end))
        .execute()
    )

    candidates: List[ReleaseCandidate] = []
    for row in resp.data or []:
        candidates.append(
            ReleaseCandidate(
                product_id=int(row["product_id"]),
                effective_pub_date=date.fromisoformat(row["effective_pub_date"]),
            )
        )
    return candidates


def fetch_banked_presales(supabase: Client, candidates: List[ReleaseCandidate]) -> Dict[int, int]:
    """
    Prefer lifecycle_snapshot.presale_commitment_total because it freezes the
    presale cohort deterministically at the pub-date boundary.

    If a snapshot is unexpectedly missing, fall back to ledger computation.
    """
    if not candidates:
        return {}

    product_ids = [c.product_id for c in candidates]
    totals = {pid: 0 for pid in product_ids}

    snapshot_resp = (
        supabase.schema("preorder")
        .table("lifecycle_snapshot")
        .select("product_id,presale_commitment_total")
        .in_("product_id", product_ids)
        .execute()
    )

    snapshot_found: set[int] = set()
    for row in snapshot_resp.data or []:
        pid = int(row["product_id"])
        totals[pid] = int(row.get("presale_commitment_total") or 0)
        snapshot_found.add(pid)

    fallback_candidates = [c for c in candidates if c.product_id not in snapshot_found]
    if not fallback_candidates:
        return totals

    candidate_map = {c.product_id: c for c in fallback_candidates}
    fallback_ids = list(candidate_map.keys())

    ledger_resp = (
        supabase.schema("preorder")
        .table("commitment_ledger")
        .select("product_id,topic,delta_qty,occurred_at")
        .in_("product_id", fallback_ids)
        .execute()
    )

    for row in ledger_resp.data or []:
        pid = int(row["product_id"])
        topic = row["topic"]
        delta = int(row["delta_qty"])
        occurred_at = datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))

        pub_boundary_et = datetime.combine(candidate_map[pid].effective_pub_date, datetime.min.time(), tzinfo=ET)
        pub_boundary_utc = pub_boundary_et.astimezone(UTC)

        if occurred_at >= pub_boundary_utc:
            continue

        if topic in ("orders/create", "orders/create_backfill", "orders/paid", "orders/cancelled", "refunds/create"):
            totals[pid] += delta

    return totals


def fetch_product_status_map(supabase: Client, product_ids: List[int]) -> Dict[int, tuple[Optional[str], Optional[date]]]:
    if not product_ids:
        return {}

    resp = (
        supabase.schema("preorder")
        .table("product_status")
        .select("product_id,status,effective_pub_date")
        .in_("product_id", product_ids)
        .execute()
    )

    status_map: Dict[int, tuple[Optional[str], Optional[date]]] = {}
    for row in resp.data or []:
        pub = row.get("effective_pub_date")
        status_map[int(row["product_id"])] = (
            row.get("status"),
            date.fromisoformat(pub) if pub else None,
        )
    return status_map


def fetch_product_metadata(product_ids: List[int]) -> Dict[int, ProductMetadata]:
    if not product_ids:
        return {}

    cfg = get_shopify_config()
    endpoint = cfg["endpoint"]
    headers = cfg["headers"]

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

    metadata: Dict[int, ProductMetadata] = {}

    for product_id in product_ids:
        gid = f"gid://shopify/Product/{product_id}"
        r = requests.post(
            endpoint,
            headers=headers,
            json={"query": query, "variables": {"id": gid}},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json().get("data", {}).get("product")
        if not data:
            continue

        barcode = ""
        edges = data.get("variants", {}).get("edges", [])
        if edges:
            barcode = edges[0]["node"].get("barcode") or ""

        metadata[product_id] = ProductMetadata(
            product_id=product_id,
            isbn=barcode.strip(),
            title=data.get("title") or "",
            author=(data.get("metafield") or {}).get("value") or "",
            publisher=data.get("vendor") or "",
        )

    return metadata


def fetch_in_week_sales(week_start: date, week_end: date) -> Dict[int, int]:
    """
    Pull Sunday->Saturday net sales directly from Shopify orders.

    createdAt is normalized UTC -> ET before inclusion in the reporting week.
    currentQuantity is used so refunds/cancellations reduce weekly sales.
    """
    cfg = get_shopify_config()
    endpoint = cfg["endpoint"]
    headers = cfg["headers"]

    week_start_et = datetime.combine(week_start, datetime.min.time(), tzinfo=ET)
    week_end_exclusive_et = datetime.combine(week_end + timedelta(days=1), datetime.min.time(), tzinfo=ET)
    start_utc = week_start_et.astimezone(UTC).isoformat()
    end_utc = week_end_exclusive_et.astimezone(UTC).isoformat()

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
        orders = r.json().get("data", {}).get("orders", {})

        for edge in orders.get("edges", []):
            order = edge["node"]
            created_at_utc = datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00"))
            created_at_et = created_at_utc.astimezone(ET)

            if created_at_et < week_start_et or created_at_et >= week_end_exclusive_et:
                continue

            for line_edge in order.get("lineItems", {}).get("edges", []):
                node = line_edge["node"]
                product = node.get("product")
                if not product:
                    continue

                pid = product.get("legacyResourceId")
                if not pid:
                    continue

                qty = node.get("currentQuantity")
                if qty is None:
                    qty = node.get("quantity", 0)

                sales[int(pid)] = sales.get(int(pid), 0) + int(qty)

        page_info = orders.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    return sales


def should_exclude_from_report(
    product_id: int,
    release_ids: set[int],
    status_map: Dict[int, tuple[Optional[str], Optional[date]]],
    report_cutoff_date: date,
) -> bool:
    """
    Exclude unreleased preorder titles from the weekly report.

    - If a product is explicitly queued in release_state for this week, include it.
    - Otherwise, if it is still an active/future preorder beyond the report week,
      exclude it from regular weekly sales.
    """
    if product_id in release_ids:
        return False

    status, pub_date = status_map.get(product_id, (None, None))
    if status in ("active_preorder", "early_stock_arrival"):
        if pub_date is None or pub_date > report_cutoff_date:
            return True

    return False


def build_rows(
    all_product_ids: List[int],
    release_ids: set[int],
    presales: Dict[int, int],
    week_sales: Dict[int, int],
    metadata: Dict[int, ProductMetadata],
    status_map: Dict[int, tuple[Optional[str], Optional[date]]],
    report_cutoff_date: date,
) -> List[ReleaseRow]:
    rows: List[ReleaseRow] = []

    for pid in all_product_ids:
        if should_exclude_from_report(pid, release_ids, status_map, report_cutoff_date):
            continue

        meta = metadata.get(pid)
        if not meta:
            continue

        isbn = (meta.isbn or "").strip()
        if len(isbn) != 13 or not (isbn.startswith("978") or isbn.startswith("979")):
            continue

        qty = int(week_sales.get(pid, 0)) + int(presales.get(pid, 0))
        if qty <= 0:
            continue

        rows.append(ReleaseRow(product_id=pid, isbn=isbn, qty=qty))

    rows.sort(key=lambda r: r.isbn)
    return rows


def write_csv(rows: List[ReleaseRow], week_end: date, output_dir: str) -> str:
    filename = f"nyt_release_report_{week_end.isoformat()}.csv"
    path = os.path.join(output_dir, filename)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ISBN", "QTY"])
        for row in rows:
            writer.writerow([row.isbn, row.qty])

    return path


def mark_reported(
    supabase: Client,
    candidates: List[ReleaseCandidate],
    week_start: date,
    week_end: date,
    csv_filename: str,
) -> None:
    if not candidates:
        return

    now_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload = []

    for c in candidates:
        payload.append(
            {
                "product_id": c.product_id,
                "effective_pub_date": str(c.effective_pub_date),
                "released_to_reporting": True,
                "release_report_week_start": str(week_start),
                "release_report_week_end": str(week_end),
                "released_at": now_utc,
                "engine_version": ENGINE_VERSION,
                "csv_filename": csv_filename,
                "updated_at": now_utc,
            }
        )

    supabase.schema("preorder").table("release_state").upsert(
        payload,
        on_conflict="product_id,effective_pub_date",
    ).execute()


def insert_release_run(
    supabase: Client,
    week_start: date,
    week_end: date,
    csv_filename: str,
    row_count: int,
    release_count: int,
    dry_run: bool,
) -> None:
    payload = {
        "week_start": str(week_start),
        "week_end": str(week_end),
        "csv_filename": csv_filename,
        "row_count": row_count,
        "release_count": release_count,
        "engine_version": ENGINE_VERSION,
        "dry_run": dry_run,
        "ran_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }

    try:
        supabase.schema("preorder").table("release_runs").insert(payload).execute()
    except Exception:
        # Non-fatal logging table write.
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", help="Any ISO date inside the target Sunday->Saturday week")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mark-reported", action="store_true")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    week_start, week_end = resolve_target_week(args.week)
    report_cutoff_date = week_end
    supabase = get_supabase()

    candidates = fetch_admin_releases(supabase, week_start, week_end)
    presales = fetch_banked_presales(supabase, candidates)
    week_sales = fetch_in_week_sales(week_start, week_end)

    release_ids = {c.product_id for c in candidates}
    all_product_ids = sorted(set(week_sales.keys()) | release_ids)

    if not all_product_ids:
        print(f"No sales or admin-scheduled releases for week {week_start} -> {week_end}")
        return

    status_map = fetch_product_status_map(supabase, all_product_ids)
    metadata = fetch_product_metadata(all_product_ids)

    rows = build_rows(
        all_product_ids=all_product_ids,
        release_ids=release_ids,
        presales=presales,
        week_sales=week_sales,
        metadata=metadata,
        status_map=status_map,
        report_cutoff_date=report_cutoff_date,
    )

    csv_path = write_csv(rows, week_end, args.output_dir)
    print(f"Wrote {len(rows)} rows to {csv_path}")

    insert_release_run(
        supabase=supabase,
        week_start=week_start,
        week_end=week_end,
        csv_filename=os.path.basename(csv_path),
        row_count=len(rows),
        release_count=len(candidates),
        dry_run=args.dry_run,
    )

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