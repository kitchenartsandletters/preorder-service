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

Upload-confirmation model (see _upload_via_playwright):
  The NYT portal FILES the data on the "Upload" click. The separate
  "Submit Spreadsheet Data" button is NOT a reliable success signal — after a
  successful upload the iframe transitions to a confirmation state and that
  button is gone / auto-handled, so waiting for it to detach times out even
  though the report was filed. Two live weeks (Sep 13-19, Sep 20-26) failed this
  way while the portal showed "You have already submitted sales for this week."
  Success is therefore determined by READING THE PORTAL'S CONFIRMATION TEXT in
  the iframe, not by the submit button. "Already submitted this week" is also a
  success (the data is filed).
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
from shopify_token import get_token_sync
from shopify_version import get_api_version

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
SHOPIFY_STORE             = os.environ["SHOP_URL"]
SHOPIFY_API_VERSION       = get_api_version()

# Confirmation phrases the portal shows inside #spreadsheet_frame after a
# successful upload / when this week is already filed. Matched case-insensitively
# as substrings. Both mean "the data is filed" → success.
_ALREADY_SUBMITTED_TEXT = "already submitted sales for this week"
_SUBMIT_CONFIRM_TEXTS = (
    _ALREADY_SUBMITTED_TEXT,
    "thank you",                    # fresh-submission confirmation (defensive)
    "successfully submitted",       # defensive alternate phrasing
    "sales have been submitted",    # defensive alternate phrasing
)


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _current_week_bounds() -> tuple[date, date]:
    """Returns the current Sunday–Saturday calendar week (queue week)."""
    today_et = datetime.now(ET).date()
    days_since_sunday = today_et.isoweekday() % 7
    week_start = today_et - timedelta(days=days_since_sunday)
    week_end   = week_start + timedelta(days=6)
    return week_start, week_end


def _sales_week_bounds() -> tuple[date, date]:
    """Returns the prior completed Sunday–Saturday week (sales week to report)."""
    queue_start, _ = _current_week_bounds()
    week_start = queue_start - timedelta(days=7)
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
    """
    Fetch all titles queued for reporting that have not yet been uploaded.
    week_start/week_end retained as parameters for API compatibility but
    no longer used as filters — all unreported queued titles are included
    regardless of which week they were staged in.
    """
    result = (
        sb.schema("preorder")
        .from_("release_state")
        .select("product_id, effective_pub_date")
        .eq("released_to_reporting", True)
        .is_("nyt_uploaded_at", "null")
        .order("release_report_week_start", desc=False)
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
    from datetime import timezone

    UTC = timezone.utc

    # Convert ET midnight boundaries to UTC correctly using ZoneInfo
    # This handles EDT (UTC-4) vs EST (UTC-5) automatically
    week_start_et = datetime.combine(week_start, datetime.min.time(), tzinfo=ET)
    week_end_exclusive_et = datetime.combine(
        week_end + timedelta(days=1), datetime.min.time(), tzinfo=ET
    )
    start_utc = week_start_et.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc   = week_end_exclusive_et.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    query_str = f"created_at:>={start_utc} created_at:<{end_utc} financial_status:paid"

    QUERY = """
    query WeekSales($q: String!, $first: Int!, $after: String) {
      orders(query: $q, first: $first, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          lineItems(first: 250) {
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

    with httpx.Client(timeout=60.0) as client:
        while True:
            variables: Dict[str, Any] = {"q": query_str, "first": 250}
            if cursor:
                variables["after"] = cursor

            token = get_token_sync()
            resp = client.post(
                f"https://{SHOPIFY_STORE}/admin/api/{SHOPIFY_API_VERSION}/graphql.json",
                json={"query": QUERY, "variables": variables},
                headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
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
    sb: Client,
) -> tuple[str, str, int]:
    filename = f"nyt_report_sales_week_{week_end.isoformat()}.csv"
    queued_ids = {int(r["product_id"]) for r in queued}

    # Fetch ISBNs for non-preorder products that sold this week
    non_preorder_pids = [pid for pid in week_sales if pid not in metadata]
    if non_preorder_pids:
        result = sb.schema("preorder").from_("product_status").select(
            "product_id, metadata_snapshot"
        ).in_("product_id", non_preorder_pids).execute()
        for row in result.data or []:
            pid = int(row["product_id"])
            snap = row.get("metadata_snapshot") or {}
            metadata[pid] = {
                "title": snap.get("title", ""),
                "isbn": (snap.get("isbn") or "").strip(),
                "author": snap.get("author", ""),
            }

    # Fetch active/future preorder IDs to exclude from regular weekly sales
    active_resp = sb.schema("preorder").from_("product_status").select(
        "product_id, effective_pub_date"
    ).in_("status", ["active_preorder", "early_stock_arrival"]).execute()
    exclude_ids: set[int] = set()
    for row in active_resp.data or []:
        pub = row.get("effective_pub_date")
        if pub and pub > str(week_end):
            exclude_ids.add(int(row["product_id"]))

    all_pids = queued_ids | set(week_sales.keys())
    rows = []

    for pid in all_pids:
        # Exclude future preorders unless explicitly queued
        if pid in exclude_ids and pid not in queued_ids:
            continue

        meta = metadata.get(pid, {})
        isbn = (meta.get("isbn") or "").strip()
        if len(isbn) != 13 or not (isbn.startswith("978") or isbn.startswith("979")):
            log.debug(f"Skipping product {pid} — invalid ISBN: {isbn!r}")
            continue

        qty = presales.get(pid, 0) + week_sales.get(pid, 0)
        if qty <= 0:
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

def _frame_confirms_submission(frame) -> Optional[str]:
    """
    Read the iframe body text and return the matched confirmation phrase if the
    portal is showing a submitted/already-submitted state, else None. Substring
    match, case-insensitive. Robust to the exact wording drifting a little.
    """
    try:
        body = (frame.locator("body").inner_text() or "").lower()
    except Exception:
        return None
    for phrase in _SUBMIT_CONFIRM_TEXTS:
        if phrase in body:
            return phrase
    return None


def _upload_via_playwright(csv_text: str, csv_filename: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Upload CSV to bestsellers.nytimes.com.

    Returns (success, reason, screenshot_b64).
      - success=True,  reason="submitted"          → fresh upload confirmed
      - success=True,  reason="already_submitted"  → week already filed (still success)
      - success=False, reason=<error string>       → genuine failure

    Success is determined by reading the portal's confirmation text inside the
    #spreadsheet_frame iframe after the Upload click — NOT by the
    "Submit Spreadsheet Data" button, which is unreliable (the portal files data
    on Upload and the submit button is gone/auto-handled afterward). A screenshot
    is captured at every terminal state.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "playwright not installed", None

    screenshot_b64: Optional[str] = None
    page = None
    browser = None

    def _shot():
        nonlocal screenshot_b64
        try:
            if page is not None:
                screenshot_b64 = base64.b64encode(page.screenshot(full_page=True)).decode()
        except Exception:
            pass

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context()
            page    = context.new_page()

            # ── 1. Login ──────────────────────────────────────────────────────
            log.info("Playwright: navigating to NYT portal login")
            page.goto("https://bestsellers.nytimes.com/login", wait_until="domcontentloaded")
            page.get_by_placeholder("Enter username").fill(NYT_PORTAL_USERNAME)
            page.get_by_placeholder("Password").fill(NYT_PORTAL_PASSWORD)
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_load_state("domcontentloaded")
            log.info("Playwright: logged in")

            # ── 2. Navigate to upload ─────────────────────────────────────────
            page.get_by_role("link", name="Upload Spreadsheet").click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2000)  # let the iframe render
            log.info("Playwright: on upload page")

            frame = page.locator("#spreadsheet_frame").content_frame

            # ── 2a. Already submitted this week? (before uploading) ───────────
            # If the portal already has this week's data, the iframe shows the
            # "already submitted" banner and there is no upload to do. This is a
            # SUCCESS — the report is filed.
            matched = _frame_confirms_submission(frame)
            if matched == _ALREADY_SUBMITTED_TEXT:
                _shot()
                log.info("Playwright: portal already has this week's submission — treating as success")
                context.close(); browser.close()
                return True, "already_submitted", screenshot_b64

            # ── 3. Write CSV to temp file ─────────────────────────────────────
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", delete=False,
                encoding="utf-8", prefix="nyt_report_"
            ) as tmp:
                tmp.write(csv_text)
                tmp_path = tmp.name

            log.info(f"Playwright: uploading {csv_filename} from {tmp_path}")

            frame.locator("#filename").set_input_files(tmp_path)
            frame.get_by_role("button", name="Upload").click()
            page.wait_for_load_state("domcontentloaded")

            # ── 4. Confirm via portal text, not the submit button ─────────────
            # The Upload click files the data. Poll the iframe for a confirmation
            # phrase. If a "Submit Spreadsheet Data" button is present and still
            # required, click it opportunistically — but do NOT depend on it.
            try:
                submit_btn = frame.get_by_role("button", name="Submit Spreadsheet Data")
                if submit_btn.count() > 0 and submit_btn.first.is_visible():
                    submit_btn.first.click()
                    log.info("Playwright: clicked Submit Spreadsheet Data (opportunistic)")
                    page.wait_for_load_state("domcontentloaded")
            except Exception as exc:
                # Non-fatal — the button being absent/unclickable is expected when
                # the upload already filed the data.
                log.info(f"Playwright: submit button not actionable ({exc}); relying on confirmation text")

            # Poll up to ~15s for the confirmation text to appear.
            matched = None
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                matched = _frame_confirms_submission(frame)
                if matched:
                    break
                page.wait_for_timeout(1000)

            _shot()

            if matched:
                log.info(f"Playwright: submission confirmed by portal text: {matched!r}")
                context.close(); browser.close()
                reason = "already_submitted" if matched == _ALREADY_SUBMITTED_TEXT else "submitted"
                return True, reason, screenshot_b64

            # No confirmation text — treat as a genuine failure, capture any hint.
            page_text = ""
            try:
                page_text = frame.locator("body").inner_text()
            except Exception:
                pass
            error_hint = "no confirmation text found after upload"
            for line in page_text.splitlines():
                line = line.strip()
                if line and any(w in line.lower() for w in ("error", "invalid", "failed", "rejected")):
                    error_hint = line[:200]
                    break
            context.close(); browser.close()
            return False, f"Upload not confirmed: {error_hint}", screenshot_b64

    except Exception as exc:
        reason = str(exc)
        log.error(f"Playwright upload failed: {reason}")
        _shot()
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
    queue_start, queue_end = _current_week_bounds()
    sales_start, sales_end = _sales_week_bounds()

    log.info(f"NYT reporter — queue week {queue_start}→{queue_end}  sales week {sales_start}→{sales_end}  dry_run={dry_run}")

    if not dry_run and _already_uploaded(sb, sales_start):
        log.info("Already successfully uploaded for this week — exiting")
        return {"skipped": True, "reason": "already_uploaded", "week_start": str(sales_start)}

    queued = _fetch_queued_titles(sb, queue_start, queue_end)
    product_ids = [int(r["product_id"]) for r in queued]
    log.info(f"Queued preorder titles: {len(queued)}")

    presales   = _fetch_presale_qtys(sb, product_ids) if product_ids else {}
    week_sales = _fetch_shopify_week_sales(sales_start, sales_end)
    metadata   = _fetch_product_metadata(sb, product_ids) if product_ids else {}

    csv_text, csv_filename, row_count = _generate_csv(
        queued, presales, week_sales, metadata, sales_end, sb
    )

    if row_count == 0:
        log.warning("CSV has 0 valid rows — aborting")
        return {"skipped": True, "reason": "zero_rows", "week_start": str(sales_start)}

    log.info(f"CSV ready: {row_count} rows → {csv_filename}")

    if dry_run:
        log.info(f"[dry_run] Would upload {row_count} rows\n{csv_text[:400]}")
        return {
            "dry_run":      True,
            "week_start":   str(sales_start),
            "week_end":     str(sales_end),
            "titles_count": len(queued),
            "row_count":    row_count,
            "csv_preview":  csv_text[:400],
        }

    # Email CSV to distribution list
    week_str = f"{sales_start.strftime('%b %-d')} – {sales_end.strftime('%b %-d, %Y')}"
    send_email(
        subject=f"NYT Report {week_str} — {row_count} titles",
        html_body=f"<p>NYT report attached for the week of {week_str}. Uploading to portal now.</p>",
        text_body=f"NYT report attached for the week of {week_str}. Uploading to portal now.",
        attachment_name=csv_filename,
        attachment_data=csv_text,
    )

    # Playwright upload
    import asyncio
    success, reason, screenshot_b64 = await asyncio.to_thread(
        _upload_via_playwright, csv_text, csv_filename
    )
    now_iso = datetime.now(UTC).isoformat()

    if success:
        # reason is "submitted" (fresh) or "already_submitted" (week already filed).
        # In BOTH cases the report is filed, so mark queued titles uploaded and
        # log success. already_submitted still marks titles: this run's queued
        # titles need nyt_uploaded_at set regardless of which run did the portal
        # submit.
        log.info(f"Upload successful ({reason})")
        _mark_titles_uploaded(sb, queued)
        _write_log(
            sb, sales_start, sales_end, csv_filename, csv_text,
            titles_count=len(queued), upload_status="success",
            fallback_reason=(f"already_submitted: portal already had this week's data"
                             if reason == "already_submitted" else None),
            uploaded_at=now_iso, screenshot_b64=screenshot_b64,
        )
        return {
            "uploaded": True, "reason": reason,
            "week_start": str(queue_start), "titles_count": len(queued), "row_count": row_count,
        }

    else:
        log.error(f"Upload failed: {reason}")
        _write_log(
            sb, sales_start, sales_end, csv_filename, csv_text,
            titles_count=len(queued), upload_status="fallback",
            fallback_reason=reason, screenshot_b64=screenshot_b64,
        )
        nyt_url = f"{ADMIN_DASHBOARD_URL}/reports/nyt"
        send_email(
            subject=f"⚠️ MANUAL UPLOAD REQUIRED — NYT Report {week_str}",
            html_body=(
                f"<html><body style='font-family:sans-serif'>"
                f"<h2 style='color:#b91c1c'>⚠️ NYT Report — Manual Upload Required</h2>"
                f"<p>Automated upload failed for <strong>{week_str}</strong>.</p>"
                f"<p><strong>Reason:</strong> {reason}</p>"
                f"<p>Upload the attached CSV at <a href='{NYT_PORTAL_URL}'>{NYT_PORTAL_URL}</a>, "
                f"then confirm in the <a href='{nyt_url}'>Admin Dashboard</a>.</p>"
                f"</body></html>"
            ),
            text_body=(
                f"NYT Report upload FAILED for {week_str}.\n"
                f"Reason: {reason}\n\n"
                f"Upload CSV manually: {NYT_PORTAL_URL}\n"
                f"Mark as reported: {nyt_url}\n"
            ),
            attachment_name=csv_filename,
            attachment_data=csv_text,
            screenshot_b64=screenshot_b64,
        )
        return {
            "uploaded": False, "fallback": True,
            "failure_reason": reason,
            "week_start": str(queue_start), "titles_count": len(queued), "row_count": row_count,
        }
