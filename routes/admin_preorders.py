from fastapi import APIRouter, HTTPException, Header, Depends
import os
import csv
import io
import requests
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi.responses import StreamingResponse
from supabase import create_client, Client
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv()

ET = ZoneInfo("America/New_York")

router = APIRouter()

# ------------------------------------------------------------------
# Environment
# ------------------------------------------------------------------

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ADMIN_TOKEN = os.getenv("PREORDER_ADMIN_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

class MarkReportedRequest(BaseModel):
    product_ids: list[int]
    week_anchor: str  # any ISO date inside the reporting week

def resolve_week_bounds(week_anchor: str | None = None) -> tuple[date, date]:
    if week_anchor:
        anchor = date.fromisoformat(week_anchor)
    else:
        anchor = datetime.now(ET).date()
    days_since_sunday = (anchor.weekday() + 1) % 7
    week_start = anchor - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end

# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------

def require_admin_token(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True


# ------------------------------------------------------------------
# Preorder Dashboard Endpoints
# ------------------------------------------------------------------

@router.get("/products")
def get_preorder_products(ok: bool = Depends(require_admin_token)):
    resp = (
        supabase
        .schema("preorder")
        .from_("vw_preorder_products")
        .select("*")
        .not_.eq("classification", "not_a_preorder_product")
        .execute()
    )

    return resp.data or []


@router.get("/release-queue")
def get_release_queue(ok: bool = Depends(require_admin_token)):
    resp = (
        supabase
        .schema("preorder")
        .from_("vw_preorder_release_queue")
        .select("*")
        .not_.eq("classification", "not_a_preorder_product")
        .execute()
    )

    return resp.data or []


@router.get("/metrics")
def get_preorder_metrics(ok: bool = Depends(require_admin_token)):
    resp = (
        supabase
        .schema("preorder")
        .from_("vw_preorder_metrics")
        .select("*")
        .single()
        .execute()
    )

    return resp.data or {
        "active_preorders": 0,
        "early_arrivals": 0,
        "anomalies": 0,
        "release_queue_count": 0,
        "released_this_week": 0
    }

@router.get("/live-metrics")
def get_live_presale_metrics(ok: bool = Depends(require_admin_token)):
    """
    Returns presale metrics derived exclusively from verified Tier 1 data
    (post-cutover live webhook events only).

    data_confidence will always be 'verified' for rows in this response.
    For estimated figures covering pre-cutover history, use /products
    which surfaces both live_presale_qty and estimated_presale_qty with
    explicit data_confidence labeling.
    """
    resp = (
        supabase
        .schema("preorder")
        .from_("vw_live_presale_metrics")
        .select("*")
        .execute()
    )

    return resp.data or []


@router.get("/upcoming")
def get_upcoming_releases(ok: bool = Depends(require_admin_token)):
    """
    Active preorders and early stock titles with pub_date within 7 days.
    Used by Release Review tab Upcoming section.
    """
    resp = (
        supabase
        .schema("preorder")
        .from_("vw_preorder_release_queue")
        .select("*")
        .eq("due_for_release_review", True)
        .execute()
    )
    return resp.data or []


@router.get("/reportable")
def get_reportable_preorders(
    week: str | None = None,
    ok: bool = Depends(require_admin_token)
):
    """
    Historical preorders eligible for NYT reporting.
    Optionally scoped to a specific reporting week via ?week=YYYY-MM-DD.
    Returns all reportable titles when no week is specified
    so the frontend can handle week filtering client-side.
    """
    resp = (
        supabase
        .schema("preorder")
        .from_("vw_reportable_preorders")
        .select("*")
        .order("pub_date", desc=True)
        .execute()
    )
    return resp.data or []


@router.post("/report/preview")
def generate_report_preview(
    payload: dict,
    ok: bool = Depends(require_admin_token)
):
    """
    Dry-run report generation. No state is written.

    For selected preorder product_ids:
        qty = bounded presale qty + post-pub in-week Shopify sales

    For all other products that sold this week:
        qty = in-week Shopify sales only

    Active/future preorders are excluded from the weekly sales pass
    so they never leak into the report before their pub date.

    Operator qty overrides (qty_overrides) replace the system presale
    qty for the specified product_ids before combining with week sales.

    Body:
      {
        "product_ids": [123, 456],
        "week_anchor": "2026-03-31",
        "qty_overrides": {"123": 19}   # optional, keyed by product_id string
      }

    Returns CSV: ISBN,QTY sorted by ISBN.
    """
    product_ids: list[int] = payload.get("product_ids", [])
    week_anchor: str | None = payload.get("week_anchor")
    raw_overrides: dict = payload.get("qty_overrides") or {}

    if not product_ids:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="product_ids required")

    # Normalize override keys to int
    qty_overrides: dict[int, int] = {
        int(k): int(v) for k, v in raw_overrides.items()
    }

    week_start, week_end = resolve_week_bounds(week_anchor)

    # --- Step 1: Bounded presale qtys for selected preorder titles ---
    presales_resp = (
        supabase
        .schema("preorder")
        .from_("vw_reportable_preorders")
        .select("product_id, isbn, title, total_presale_qty, data_confidence")
        .in_("product_id", product_ids)
        .execute()
    )
    presale_map: dict[int, dict] = {
        int(row["product_id"]): row
        for row in presales_resp.data or []
    }

    # --- Step 2: All in-week Shopify sales (full store, all products) ---
    shopify_sales = _fetch_shopify_week_sales(week_start, week_end)

    # --- Step 3: Active/future preorder IDs to exclude from weekly sales ---
    # These titles have not yet published — they must not appear as
    # regular weekly sales even if Shopify recorded orders this week.
    active_resp = (
        supabase
        .schema("preorder")
        .from_("product_status")
        .select("product_id, effective_pub_date")
        .in_("status", ["active_preorder", "early_stock_arrival"])
        .execute()
    )
    exclude_ids: set[int] = set()
    for row in active_resp.data or []:
        pub = row.get("effective_pub_date")
        if pub and pub > str(week_end):
            exclude_ids.add(int(row["product_id"]))

    # --- Step 4: ISBNs for non-preorder products that sold this week ---
    # presale_map already has ISBNs for preorder titles.
    # For everything else, look up ISBN from product_status metadata.
    isbn_map: dict[int, str] = {
        pid: (row.get("isbn") or "").strip()
        for pid, row in presale_map.items()
    }
    non_preorder_pids = [
        pid for pid in shopify_sales
        if pid not in isbn_map
    ]
    if non_preorder_pids:
        meta_resp = (
            supabase
            .schema("preorder")
            .from_("product_status")
            .select("product_id, metadata_snapshot")
            .in_("product_id", non_preorder_pids)
            .execute()
        )
        for row in meta_resp.data or []:
            pid = int(row["product_id"])
            snapshot = row.get("metadata_snapshot") or {}
            isbn_map[pid] = (snapshot.get("isbn") or "").strip()

    # Add this temporary debug block in generate_report_preview
    # after fetching shopify_sales and before building rows:
    print(f"[DEBUG] shopify_sales count qty: {len(shopify_sales)}")
    print(f"[DEBUG] presale_map pids: {list(presale_map.keys())}")
    print(f"[DEBUG] non_preorder_pids count: {len(non_preorder_pids)}")
    print(f"[DEBUG] isbn_map after meta fetch: {len(isbn_map)} entries")
    print(f"[DEBUG] exclude_ids count: {len(exclude_ids)}")

    # --- Step 5: Build combined report rows ---
    # Union of selected preorder IDs and all products in weekly Shopify sales
    all_pids = set(product_ids) | set(shopify_sales.keys())

    rows: list[dict] = []
    for pid in all_pids:
        # Exclude future preorders from the weekly sales pass.
        # Selected preorder IDs are always included regardless.
        if pid in exclude_ids and pid not in product_ids:
            continue

        isbn = isbn_map.get(pid, "")
        if len(isbn) != 13 or not (isbn.startswith("978") or isbn.startswith("979")):
            continue

        # Apply operator override if present, otherwise use system presale qty
        if pid in qty_overrides:
            presale_qty = qty_overrides[pid]
        else:
            presale_qty = int((presale_map.get(pid) or {}).get("total_presale_qty") or 0)

        week_qty = int(shopify_sales.get(pid, 0))
        total = presale_qty + week_qty

        if total <= 0:
            continue

        rows.append({
            "isbn": isbn,
            "qty": total,
            "presale_qty": presale_qty,
            "week_qty": week_qty,
        })

    rows.sort(key=lambda r: r["isbn"])

    # --- Step 6: Stream CSV ---
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ISBN", "QTY"])
    for r in rows:
        writer.writerow([r["isbn"], r["qty"]])

    filename = f"nyt_preview_{week_end.isoformat()}.csv"
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.post("/mark-reported")
def mark_reported(
    payload: MarkReportedRequest,
    ok: bool = Depends(require_admin_token)
):
    """
    Manually marks selected titles as reported for a given reporting week.
    Writes release_state rows with released_to_reporting = true.
    Idempotent — safe to call multiple times for the same product/week.
    """
    week_start, week_end = resolve_week_bounds(payload.week_anchor)

    if not payload.product_ids:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="product_ids required")

    # Fetch pub dates for the selected products
    pub_date_resp = (
        supabase
        .schema("preorder")
        .from_("product_status")
        .select("product_id, effective_pub_date")
        .in_("product_id", payload.product_ids)
        .execute()
    )

    pub_date_map = {
        row["product_id"]: row["effective_pub_date"]
        for row in pub_date_resp.data or []
    }

    now_utc = datetime.utcnow().isoformat() + "Z"

    rows = []
    for pid in payload.product_ids:
        pub_date = pub_date_map.get(pid)
        if not pub_date:
            continue
        rows.append({
            "product_id": pid,
            "effective_pub_date": pub_date,
            "released_to_reporting": True,
            "release_report_week_start": str(week_start),
            "release_report_week_end": str(week_end),
            "released_at": now_utc,
            "engine_version": "admin-dashboard-manual",
            "csv_filename": None,
            "updated_at": now_utc,
        })

    if rows:
        supabase.schema("preorder").table("release_state").upsert(
            rows,
            on_conflict="product_id,effective_pub_date"
        ).execute()

    return {
        "marked": len(rows),
        "week_start": str(week_start),
        "week_end": str(week_end),
        "product_ids": payload.product_ids,
    }

def _fetch_shopify_week_sales(week_start: date, week_end: date) -> dict[int, int]:
    """
    Pull net weekly sales from Shopify for the reporting window.
    Uses currentQuantity so refunds reduce the count.
    Mirrors services/weekly_release_engine.py fetch_in_week_sales logic.
    """
    shop = os.getenv("SHOP_URL")
    token = os.getenv("SHOPIFY_ACCESS_TOKEN")
    api_version = os.getenv("API_VERSION", "2025-10")
    endpoint = f"https://{shop}/admin/api/{api_version}/graphql.json"
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": token,
    }

    from datetime import timezone
    UTC = timezone.utc
    week_start_et = datetime.combine(week_start, datetime.min.time(), tzinfo=ET)
    week_end_et = datetime.combine(week_end + timedelta(days=1), datetime.min.time(), tzinfo=ET)
    start_utc = week_start_et.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc = week_end_et.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    query = """
    query Orders($cursor: String, $query: String!) {
      orders(first: 250, after: $cursor, query: $query) {
        pageInfo { hasNextPage endCursor }
        edges {
          node {
            lineItems(first: 50) {
              edges {
                node {
                  product { legacyResourceId }
                  currentQuantity
                }
              }
            }
          }
        }
      }
    }
    """

    sales: dict[int, int] = {}
    cursor = None

    while True:
        variables = {
            "query": f"created_at:>={start_utc} created_at:<{end_utc} financial_status:paid",
            "cursor": cursor,
        }
        r = requests.post(
            endpoint,
            headers=headers,
            json={"query": query, "variables": variables},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data", {}).get("orders", {})

        for edge in data.get("edges", []):
            for li in edge["node"]["lineItems"]["edges"]:
                node = li["node"]
                product = node.get("product")
                if not product:
                    continue
                pid = int(product["legacyResourceId"])
                qty = int(node.get("currentQuantity") or 0)
                sales[pid] = sales.get(pid, 0) + qty

        page_info = data.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    return sales