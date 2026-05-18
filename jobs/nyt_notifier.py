"""
jobs/nyt_notifier.py

Monday morning notifier — WS4 NYT Reporting automation.

Checks whether any preorder titles are queued for this week's NYT report.
If yes: emails EMAIL_RECIPIENTS with a summary + link to /reports/nyt.
If no:  exits silently (no email, no log row).

Called by jobs/run.py --job nyt_notifier
Railway cron: 0 14 * * 1  (9am ET Monday, UTC 14:00)
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from supabase import create_client, Client

from jobs.mailtrap import send_email

log = logging.getLogger(__name__)

ET  = ZoneInfo("America/New_York")
UTC = timezone.utc

# ── Env ───────────────────────────────────────────────────────────────────────
SUPABASE_URL              = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ADMIN_DASHBOARD_URL       = os.getenv("ADMIN_DASHBOARD_URL", "https://admin.kitchenartsandletters.com")


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _current_week_bounds() -> tuple[date, date]:
    """Return Sunday→Saturday bounds for the current ET week."""
    today_et = datetime.now(ET).date()
    days_since_sunday = today_et.weekday() + 1
    if today_et.weekday() == 6:
        days_since_sunday = 0
    week_start = today_et - timedelta(days=days_since_sunday)
    week_end   = week_start + timedelta(days=6)
    return week_start, week_end


def _fetch_queued_titles(sb: Client, week_start: date, week_end: date) -> List[Dict[str, Any]]:
    result = (
        sb.schema("preorder")
        .from_("release_state")
        .select(
            "product_id, effective_pub_date, release_report_week_start, "
            "release_report_week_end, released_at, nyt_uploaded_at"
        )
        .eq("released_to_reporting", True)
        .is_("nyt_uploaded_at", "null")
        .gte("release_report_week_start", str(week_start))
        .lte("release_report_week_end",   str(week_end))
        .execute()
    )
    return result.data or []


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
            "title":  snap.get("title", f"Product {row['product_id']}"),
            "isbn":   snap.get("isbn", "—"),
            "author": snap.get("author", "—"),
        }
    return out


def _build_email(
    titles: List[Dict],
    metadata: Dict[int, Dict],
    week_start: date,
    week_end: date,
) -> tuple[str, str]:
    nyt_url  = f"{ADMIN_DASHBOARD_URL}/reports/nyt"
    week_str = f"{week_start.strftime('%b %-d')} – {week_end.strftime('%b %-d, %Y')}"
    count    = len(titles)

    rows_html = ""
    rows_text = ""
    for t in titles:
        pid  = int(t["product_id"])
        meta = metadata.get(pid, {})
        pub  = t.get("effective_pub_date", "—")
        rows_html += (
            f"<tr>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e5e7eb'>{meta.get('isbn','—')}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e5e7eb'>{meta.get('title','—')}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e5e7eb'>{meta.get('author','—')}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e5e7eb'>{pub}</td>"
            f"</tr>"
        )
        rows_text += f"  • {meta.get('isbn','—')}  {meta.get('title','—')}  ({pub})\n"

    html = f"""
<html><body style='font-family:sans-serif;color:#111;max-width:600px;margin:auto'>
  <h2 style='color:#1f2937'>NYT Report — {week_str}</h2>
  <p>{count} title{'s' if count != 1 else ''} queued for this week's NYT bestseller report.</p>
  <table style='width:100%;border-collapse:collapse;font-size:13px'>
    <thead>
      <tr style='background:#f3f4f6;text-align:left'>
        <th style='padding:6px 12px'>ISBN</th>
        <th style='padding:6px 12px'>Title</th>
        <th style='padding:6px 12px'>Author</th>
        <th style='padding:6px 12px'>Pub Date</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  <p style='margin-top:24px'>
    <a href='{nyt_url}' style='background:#1d4ed8;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:600'>
      Review in Admin Dashboard →
    </a>
  </p>
  <p style='font-size:11px;color:#9ca3af;margin-top:32px'>
    Kitchen Arts &amp; Letters · Preorder Service · NYT Reporting
  </p>
</body></html>
"""

    text = (
        f"NYT Report — {week_str}\n\n"
        f"{count} title{'s' if count != 1 else ''} queued for this week's NYT bestseller report:\n\n"
        f"{rows_text}\n"
        f"Review and trigger report: {nyt_url}\n"
    )

    return html, text


async def run(limit: int = 2000, dry_run: bool = False) -> Dict[str, Any]:
    """Entry point called by jobs/run.py."""
    sb = _get_supabase()
    week_start, week_end = _current_week_bounds()
    log.info(f"NYT notifier — checking week {week_start} → {week_end}")

    titles = _fetch_queued_titles(sb, week_start, week_end)

    if not titles:
        log.info("No queued titles for this week — no notification sent")
        return {"notified": False, "titles_count": 0, "week_start": str(week_start), "week_end": str(week_end)}

    product_ids = [int(t["product_id"]) for t in titles]
    metadata    = _fetch_product_metadata(sb, product_ids)

    subject = f"NYT Report Ready — {len(titles)} title{'s' if len(titles) != 1 else ''} queued ({week_start.strftime('%b %-d')})"
    html, text = _build_email(titles, metadata, week_start, week_end)

    if dry_run:
        log.info(f"[dry_run] Would email — subject: {subject}")
    else:
        send_email(subject=subject, html_body=html, text_body=text)
        # Upsert a placeholder log row so notified_at is recorded
        sb.schema("preorder").from_("nyt_report_log").upsert(
            {
                "week_start":    str(week_start),
                "week_end":      str(week_end),
                "upload_status": "error",    # placeholder until reporter runs
                "titles_count":  len(titles),
                "notified_at":   datetime.now(UTC).isoformat(),
            },
            on_conflict="week_start,week_end",
            ignore_duplicates=False,
        ).execute()

    log.info(f"Notified — {len(titles)} titles queued for {week_start}")
    return {
        "notified":     not dry_run,
        "dry_run":      dry_run,
        "titles_count": len(titles),
        "week_start":   str(week_start),
        "week_end":     str(week_end),
    }