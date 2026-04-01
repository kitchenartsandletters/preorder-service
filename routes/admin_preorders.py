from fastapi import APIRouter, HTTPException, Header, Depends
import os
import csv
import io
import requests
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi.responses import StreamingResponse
from supabase import create_client, Client

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
    Combines banked presales for selected product_ids with
    live Shopify in-week sales for the target reporting week.

    Body:
      {
        "product_ids": [123, 456],   # product_ids operator selected
        "week_anchor": "2026-03-31"  # any date inside target week
      }

    Returns CSV as a streaming download: ISBN,QTY
    """
    product_ids: list[int] = payload.get("product_ids", [])
    week_anchor: str | None = payload.get("week_anchor")

    if not product_ids:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="product_ids required")

    week_start, week_end = resolve_week_bounds(week_anchor)

    # --- Presale quantities for selected products ---
    presales_resp = (
        supabase
        .schema("preorder")
        .from_("vw_reportable_preorders")
        .select("product_id, isbn, title, total_presale_qty, data_confidence")
        .in_("product_id", product_ids)
        .execute()
    )

    presale_map: dict[int, dict] = {}
    for row in presales_resp.data or []:
        presale_map[int(row["product_id"])] = row

    # --- In-week Shopify sales ---
    shopify_sales = _fetch_shopify_week_sales(week_start, week_end)

    # --- Combine and build CSV rows ---
    rows: list[dict] = []
    for pid in product_ids:
        meta = presale_map.get(pid)
        if not meta:
            continue

        isbn = (meta.get("isbn") or "").strip()
        if len(isbn) != 13 or not (isbn.startswith("978") or isbn.startswith("979")):
            continue

        presale_qty = int(meta.get("total_presale_qty") or 0)
        week_qty = int(shopify_sales.get(pid, 0))
        total = presale_qty + week_qty

        if total <= 0:
            continue

        rows.append({
            "isbn": isbn,
            "qty": total,
            "title": meta.get("title", ""),
            "presale_qty": presale_qty,
            "week_qty": week_qty,
            "data_confidence": meta.get("data_confidence", "estimated"),
        })

    rows.sort(key=lambda r: r["isbn"])

    # --- Stream CSV ---
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
    start_utc = week_start_et.astimezone(UTC).isoformat()
    end_utc = week_end_et.astimezone(UTC).isoformat()

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