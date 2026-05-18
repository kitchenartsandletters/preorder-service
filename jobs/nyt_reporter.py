"""
jobs/nyt_reporter.py

NYT Report generation + upload job — WS4 NYT Reporting automation.

Flow:
  1. Check release_state for this week's queued (not yet uploaded) titles
  2. Check nyt_report_log — if success row already exists, exit (idempotent)
  3. Generate CSV via weekly_release_engine logic
  4. Email CSV to recipients via Mailtrap
  5. Playwright → upload to bestsellers.nytimes.com
     → success: mark release_state.nyt_uploaded_at, write log(status=success)
     → failure: email fallback alert with CSV + screenshot, write log(status=fallback)

Called by jobs/run.py --job nyt_reporter
Idempotency: will not re-upload if nyt_report_log already has status='success' for this week.
"""

from __future__ import annotations

import base64
import csv
import io
import logging
import os
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from supabase import create_client, Client

from jobs.mailtrap import send_email

log = logging.getLogger(__name__)

ET  = ZoneInfo("America/New_York")
UTC = timezone.utc

ENGINE_VERSION = "nyt-reporter-v1"

# ── Env ───────────────────────────────────────────────────────────────────────
SUPABASE_URL              = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ADMIN_DASHBOARD_URL       = os.getenv("ADMIN_DASHBOARD_URL", "https://admin.kitchenartsandletters.com")
NYT_PORTAL_URL            = os.getenv("NYT_PORTAL_URL", "https://bestsellers.nytimes.com")
NYT_PORTAL_USERNAME       = os.environ["NYT_PORTAL_USERNAME"]
NYT_PORTAL_PASSWORD       = os.environ["NYT_PORTAL_PASSWORD"]
SHOPIFY_STORE             = os.environ["SHOPIFY_STORE"]
SHOPIFY_ACCESS_TOKEN      = os.environ["SHOPIFY_ACCESS_TOKEN"]
SHOPIFY_API_VERSION       = os.getenv("SHOPIFY_API_VERSION", "2025-01")


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _current_week_bounds() -> tuple[date, date]:
    today_et = datetime.now(ET).date()
    days_since_sunday = today_et.weekday() + 1
    if today_et.weekday() == 6:
        days_since_sunday = 0
    week_start = today_et - timedelta(days=days_since_sunday)
    week_end   = week_start + timedelta(days=6)
    return week_start, week_end


# ── Idempotency ───────────────────────────────────────────────────────────────

def _already_uploaded(sb: Client, week_start: date) -> bool:
    result = (
        sb.schema("preorder")
        .from_("nyt_report_log")
        .select("id")
        .eq("week_start", str(week_start))
        .eq("upload_status", "success")
        .limit(1)
        .execute()
    )
    return bool(result.data)


# ── Data fetching ─────────────────────────────────────────────────────────────

def _fetch_queued_titles(sb: Client, week_start: date, week_end: date) -> List[Dict]:
    result = (
        sb.schema("preorder")
        .from_("release_state")
        .select("product_id, effective_pub_date")
        .eq("released_to_reporting", True)
        .is_("nyt_uploaded_at", "null")
        .gte("release_report_week_start", str(week_start))
        .lte("release_report_week_end",   str(week_end))
        .execute()
    )
    return result.data or []


def _fetch_presale_qtys(sb: Client, product_ids: List[int]) -> Dict[int, int]:
    if not product_ids:
        return {}
    result = (
        sb.schema("preorder")
        .from_("vw_reportable_preorders")
        .select("product_id, total_presale_qty")
        .in_("product_id", product_ids)
        .execute()
    )
    return {int(r["product_id"]): int(r["total_presale_qty"] or 0) for r in result.data or []}


def _fetch_product_metadata(sb: Client, product_ids: List[int]) -> Dict[int, Dict]:
    if not product_ids:
        return {}
    result = (
        sb.schema("preorder")
        .from_("product_status")
        .select("product_id, metadata_snapshot")
        .in_("product_id", product_ids)
        .execute()
    )
    out = {}
    for row in result.data or []:
        snap = row.get("metadata_snapshot") or {}
        out[int(row["product_id"])] = {
            "title":  snap.get("title", ""),
            "isbn":   snap.get("isbn", ""),
            "author": snap.get("author", ""),
        }
    return out


def _fetch_shopify_week_sales(week_start: date, week_end: date) -> Dict[int, int]:
    import httpx

    start_iso = f"{week_start}T00:00:00-05:00"
    end_iso   = f"{week_end}T23:59:59-05:00"
    query_str = f"created_at:>={start_iso} created_at:<={end_iso} financial_status:paid"

    QUERY = """
    query WeekSales($q: String!, $first: Int!, $after: String) {
      orders(query: $q, first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          lineItems(first: 50) {
            nodes {
              product { id }
              currentQuantity
            }
          }
        }
      }
    }
    """

    sales: Dict[int, int] = {}
    cursor = None

    with httpx.Client(timeout=30.0) as client:
        while True:
            variables: Dict[str, Any] = {"q": query_str, "first": 50}
            if cursor:
                variables["after"] = cursor
            resp = client.post(
                f"https://{SHOPIFY_STORE}/admin/api/{SHOPIFY_API_VERSION}/graphql.json",
                json={"query": QUERY, "variables": variables},
                headers={"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()["data"]["orders"]
            for order in data["nodes"]:
                for item in order["lineItems"]["nodes"]:
                    product = item.get("product") or {}
                    gid = product.get("id", "")
                    if not gid:
                        continue
                    try:
                        pid = int(gid.split("/")[-1])
                    except ValueError:
                        continue
                    sales[pid] = sales.get(pid, 0) + int(item.get("currentQuantity") or 0)
            if not data["pageInfo"]["hasNextPage"]:
                break
            cursor = data["pageInfo"]["endCursor"]

    return sales


# ── CSV generation ────────────────────────────────────────────────────────────

def _generate_csv(
    queued: List[Dict],
    presales: Dict[int, int],
    week_sales: Dict[int, int],
    metadata: Dict[int, Dict],
    week_end: date,
) -> tuple[str, str, int]:
    filename = f"nyt_report_{week_end.isoformat()}.csv"
    queued_ids = {int(r["product_id"]) for r in queued}
    rows = []

    for pid in queued_ids:
        meta = metadata.get(pid, {})
        isbn = (meta.get("isbn") or "").strip()
        if len(isbn) != 13 or not (isbn.startswith("978") or isbn.startswith("979")):
            log.warning(f"Skipping product {pid} — invalid ISBN: {isbn!r}")
            continue
        qty = presales.get(pid, 0) + week_sales.get(pid, 0)
        if qty <= 0:
            log.warning(f"Skipping product {pid} — qty is 0")
            continue
        rows.append({"isbn": isbn, "qty": qty})

    rows.sort(key=lambda r: r["isbn"])

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ISBN", "QTY"])
    for r in rows:
        writer.writerow([r["isbn"], r["qty"]])

    return buf.getvalue(), filename, len(rows)


# ── Playwright upload ─────────────────────────────────────────────────────────

def _upload_via_playwright(csv_text: str, csv_filename: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Upload CSV to bestsellers.nytimes.com.
    Returns (success, failure_reason, screenshot_b64).

    NOTE: Selectors below are templates — validate against the live portal
    using: playwright codegen https://bestsellers.nytimes.com
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "playwright not installed", None

    screenshot_b64: Optional[str] = None
    page = None
    browser = None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page    = browser.new_page()

            # 1. Login
            log.info("Playwright: navigating to NYT portal")
            page.goto(NYT_PORTAL_URL, wait_until="networkidle", timeout=30_000)
            page.fill('input[name="username"], input[type="email"]', NYT_PORTAL_USERNAME)
            page.fill('input[name="password"], input[type="password"]', NYT_PORTAL_PASSWORD)
            page.click('button[type="submit"], input[type="submit"]')
            page.wait_for_load_state("networkidle", timeout=20_000)

            # 2. Navigate to upload
            log.info("Playwright: finding upload section")
            upload_link = page.locator("a[href*='upload'], a[href*='report'], a:has-text('Upload')")
            upload_link.first.click()
            page.wait_for_load_state("networkidle", timeout=15_000)

            # 3. Write CSV to temp file and set on file input
            with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as tmp:
                tmp.write(csv_text)
                tmp_path = tmp.name

            log.info(f"Playwright: uploading {csv_filename}")
            page.locator('input[type="file"]').set_input_files(tmp_path)
            time.sleep(1)

            # 4. Submit
            page.locator('button[type="submit"], input[type="submit"], button:has-text("Submit"), button:has-text("Upload")').first.click()
            page.wait_for_load_state("networkidle", timeout=20_000)

            # 5. Confirm success — adjust selector to match portal's actual success state
            success = page.locator("text=successfully, text=thank you, text=received, .success, [class*='success']")
            if success.count() == 0:
                raise RuntimeError("No success indicator found after submission")

            browser.close()
            log.info("Playwright: upload confirmed")
            return True, None, None

    except Exception as exc:
        reason = str(exc)
        log.error(f"Playwright upload failed: {reason}")
        try:
            png = page.screenshot()
            screenshot_b64 = base64.b64encode(png).decode()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        return False, reason, screenshot_b64


# ── Supabase writes ───────────────────────────────────────────────────────────

def _mark_titles_uploaded(sb: Client, queued: List[Dict]) -> None:
    now = datetime.now(UTC).isoformat()
    for row in queued:
        sb.schema("preorder").from_("release_state").update(
            {"nyt_uploaded_at": now}
        ).eq("product_id", row["product_id"]).eq(
            "effective_pub_date", row["effective_pub_date"]
        ).execute()


def _write_log(
    sb: Client,
    week_start: date,
    week_end: date,
    csv_filename: str,
    csv_content: str,
    titles_count: int,
    upload_status: str,
    fallback_reason: Optional[str] = None,
    screenshot_b64: Optional[str] = None,
    uploaded_at: Optional[str] = None,
) -> None:
    sb.schema("preorder").from_("nyt_report_log").upsert(
        {
            "week_start":      str(week_start),
            "week_end":        str(week_end),
            "csv_filename":    csv_filename,
            "csv_content":     csv_content,
            "titles_count":    titles_count,
            "upload_status":   upload_status,
            "fallback_reason": fallback_reason,
            "screenshot_b64":  screenshot_b64,
            "uploaded_at":     uploaded_at,
        },
        on_conflict="week_start,week_end",
    ).execute()


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(limit: int = 2000, dry_run: bool = False) -> Dict[str, Any]:
    """Called by jobs/run.py --job nyt_reporter."""
    sb = _get_supabase()
    week_start, week_end = _current_week_bounds()

    log.info(f"NYT reporter — week {week_start} → {week_end}  dry_run={dry_run}")

    if not dry_run and _already_uploaded(sb, week_start):
        log.info("Already successfully uploaded for this week — exiting")
        return {"skipped": True, "reason": "already_uploaded", "week_start": str(week_start)}

    queued = _fetch_queued_titles(sb, week_start, week_end)
    if not queued:
        log.info("No queued titles — nothing to report")
        return {"skipped": True, "reason": "no_queued_titles", "week_start": str(week_start)}

    product_ids = [int(r["product_id"]) for r in queued]
    log.info(f"Queued: {len(queued)} titles")

    presales   = _fetch_presale_qtys(sb, product_ids)
    week_sales = _fetch_shopify_week_sales(week_start, week_end)
    metadata   = _fetch_product_metadata(sb, product_ids)

    csv_text, csv_filename, row_count = _generate_csv(queued, presales, week_sales, metadata, week_end)

    if row_count == 0:
        log.warning("CSV has 0 valid rows — aborting")
        return {"skipped": True, "reason": "zero_rows", "week_start": str(week_start)}

    log.info(f"CSV ready: {row_count} rows → {csv_filename}")

    if dry_run:
        log.info(f"[dry_run] Would upload {row_count} rows\n{csv_text[:400]}")
        return {
            "dry_run":      True,
            "week_start":   str(week_start),
            "week_end":     str(week_end),
            "titles_count": len(queued),
            "row_count":    row_count,
            "csv_preview":  csv_text[:400],
        }

    # Email CSV to distribution list
    week_str = f"{week_start.strftime('%b %-d')} – {week_end.strftime('%b %-d, %Y')}"
    send_email(
        subject=f"NYT Report {week_str} — {row_count} titles",
        html_body=f"<p>NYT report attached for the week of {week_str}. Uploading to portal now.</p>",
        text_body=f"NYT report attached for the week of {week_str}. Uploading to portal now.",
        attachment_name=csv_filename,
        attachment_data=csv_text,
    )

    # Playwright upload
    success, failure_reason, screenshot_b64 = _upload_via_playwright(csv_text, csv_filename)
    now_iso = datetime.now(UTC).isoformat()

    if success:
        log.info("Upload successful")
        _mark_titles_uploaded(sb, queued)
        _write_log(
            sb, week_start, week_end, csv_filename, csv_text,
            titles_count=len(queued), upload_status="success", uploaded_at=now_iso,
        )
        return {"uploaded": True, "week_start": str(week_start), "titles_count": len(queued), "row_count": row_count}

    else:
        log.error(f"Upload failed: {failure_reason}")
        _write_log(
            sb, week_start, week_end, csv_filename, csv_text,
            titles_count=len(queued), upload_status="fallback",
            fallback_reason=failure_reason, screenshot_b64=screenshot_b64,
        )
        nyt_url = f"{ADMIN_DASHBOARD_URL}/reports/nyt"
        send_email(
            subject=f"⚠️ MANUAL UPLOAD REQUIRED — NYT Report {week_str}",
            html_body=(
                f"<html><body style='font-family:sans-serif'>"
                f"<h2 style='color:#b91c1c'>⚠️ NYT Report — Manual Upload Required</h2>"
                f"<p>Automated upload failed for <strong>{week_str}</strong>.</p>"
                f"<p><strong>Reason:</strong> {failure_reason}</p>"
                f"<p>Upload the attached CSV at <a href='{NYT_PORTAL_URL}'>{NYT_PORTAL_URL}</a>, "
                f"then confirm in the <a href='{nyt_url}'>Admin Dashboard</a>.</p>"
                f"</body></html>"
            ),
            text_body=(
                f"NYT Report upload FAILED for {week_str}.\n"
                f"Reason: {failure_reason}\n\n"
                f"Upload CSV manually: {NYT_PORTAL_URL}\n"
                f"Mark as reported: {nyt_url}\n"
            ),
            attachment_name=csv_filename,
            attachment_data=csv_text,
            screenshot_b64=screenshot_b64,
        )
        return {
            "uploaded": False, "fallback": True,
            "failure_reason": failure_reason,
            "week_start": str(week_start), "titles_count": len(queued), "row_count": row_count,
        }