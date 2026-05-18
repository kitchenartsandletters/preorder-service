"""
routes/admin_nyt.py — NYT Reporting endpoints for the Admin Dashboard
=====================================================================
Endpoints:
  GET  /nyt/queue              — Current week's queued titles
  GET  /nyt/log                — Upload history (nyt_report_log)
  GET  /nyt/log/{log_id}/csv   — Re-download a past CSV
  POST /nyt/trigger            — Trigger nyt_reporter job immediately
  POST /nyt/mark-uploaded      — Manual fallback: mark titles as uploaded
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from supabase import create_client, Client
from fastapi.responses import PlainTextResponse

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

ET  = ZoneInfo("America/New_York")
UTC = timezone.utc

# ── Env + clients ─────────────────────────────────────────────────────────────
SUPABASE_URL              = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ADMIN_TOKEN               = os.getenv("PREORDER_ADMIN_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# ── Auth ──────────────────────────────────────────────────────────────────────
def require_admin_token(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True


# ── Week helpers ──────────────────────────────────────────────────────────────
def _current_week_bounds() -> tuple[date, date]:
    today_et = datetime.now(ET).date()
    days_since_sunday = today_et.weekday() + 1
    if today_et.weekday() == 6:
        days_since_sunday = 0
    week_start = today_et - timedelta(days=days_since_sunday)
    week_end   = week_start + timedelta(days=6)
    return week_start, week_end


# ── Request models ────────────────────────────────────────────────────────────
class MarkUploadedRequest(BaseModel):
    product_ids: List[int]
    week_anchor: Optional[str] = None   # YYYY-MM-DD, defaults to current week


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/nyt/queue")
def get_nyt_queue(ok: bool = Depends(require_admin_token)):
    """
    Current week's queued titles: released_to_reporting=true, nyt_uploaded_at=null.
    Enriched with title/isbn from product_status metadata_snapshot.
    """
    week_start, week_end = _current_week_bounds()

    queue_resp = (
        supabase.schema("preorder")
        .from_("release_state")
        .select("product_id, effective_pub_date, released_at, release_report_week_start, release_report_week_end")
        .eq("released_to_reporting", True)
        .is_("nyt_uploaded_at", "null")
        .gte("release_report_week_start", str(week_start))
        .lte("release_report_week_end",   str(week_end))
        .execute()
    )
    queued = queue_resp.data or []

    if not queued:
        return {"queued": [], "week_start": str(week_start), "week_end": str(week_end)}

    product_ids = [int(r["product_id"]) for r in queued]
    meta_resp = (
        supabase.schema("preorder")
        .from_("product_status")
        .select("product_id, metadata_snapshot")
        .in_("product_id", product_ids)
        .execute()
    )
    meta_map = {
        int(r["product_id"]): r.get("metadata_snapshot") or {}
        for r in meta_resp.data or []
    }

    enriched = []
    for row in queued:
        pid  = int(row["product_id"])
        snap = meta_map.get(pid, {})
        enriched.append({
            **row,
            "title":  snap.get("title", "—"),
            "isbn":   snap.get("isbn",  "—"),
            "author": snap.get("author","—"),
        })

    return {"queued": enriched, "week_start": str(week_start), "week_end": str(week_end)}


@router.get("/nyt/log")
def get_nyt_log(
    limit: int = 10,
    ok: bool = Depends(require_admin_token),
):
    """Upload history from nyt_report_log, newest first."""
    result = (
        supabase.schema("preorder")
        .from_("nyt_report_log")
        .select(
            "id, week_start, week_end, csv_filename, titles_count, "
            "upload_status, fallback_reason, notified_at, uploaded_at, created_at"
            # csv_content and screenshot_b64 intentionally excluded — large fields
        )
        .order("week_start", desc=True)
        .limit(limit)
        .execute()
    )
    return {"log": result.data or []}


@router.get("/nyt/log/{log_id}/csv", response_class=PlainTextResponse)
def download_log_csv(log_id: str, ok: bool = Depends(require_admin_token)):
    """Re-download the stored CSV for a past report run."""
    result = (
        supabase.schema("preorder")
        .from_("nyt_report_log")
        .select("csv_filename, csv_content")
        .eq("id", log_id)
        .single()
        .execute()
    )
    if not result.data or not result.data.get("csv_content"):
        raise HTTPException(status_code=404, detail="CSV not found for this log entry")

    from fastapi.responses import Response
    return Response(
        content=result.data["csv_content"],
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{result.data["csv_filename"]}"'},
    )


@router.post("/nyt/trigger")
def trigger_nyt_reporter(
    dry_run: bool = False,
    ok: bool = Depends(require_admin_token),
):
    """
    Trigger the nyt_reporter job immediately (runs in background).
    Returns immediately — poll /nyt/log to see the result.
    """
    async def _run():
        try:
            from jobs.nyt_reporter import run as run_reporter
            result = await run_reporter(dry_run=dry_run)
            logger.info(f"nyt_reporter completed: {result}")
        except Exception as exc:
            logger.error(f"nyt_reporter failed: {exc}", exc_info=True)

    asyncio.create_task(_run())

    return {
        "triggered": True,
        "dry_run":   dry_run,
        "message":   "Reporter job started. Poll /admin/preorders/nyt/log for results.",
    }


@router.post("/nyt/mark-uploaded")
def mark_uploaded_manually(
    payload: MarkUploadedRequest,
    ok: bool = Depends(require_admin_token),
):
    """
    Manual fallback: marks titles as uploaded when the user has uploaded
    the CSV to the portal themselves after a Playwright failure.
    Also flips nyt_report_log to status='success' for the current week.
    """
    if not payload.product_ids:
        raise HTTPException(status_code=422, detail="product_ids required")

    if payload.week_anchor:
        anchor = date.fromisoformat(payload.week_anchor)
        days_since_sunday = anchor.weekday() + 1
        if anchor.weekday() == 6:
            days_since_sunday = 0
        week_start = anchor - timedelta(days=days_since_sunday)
        week_end   = week_start + timedelta(days=6)
    else:
        week_start, week_end = _current_week_bounds()

    now_iso = datetime.now(UTC).isoformat()

    # Fetch effective_pub_date for each product so we can match release_state PK
    pub_resp = (
        supabase.schema("preorder")
        .from_("release_state")
        .select("product_id, effective_pub_date")
        .in_("product_id", payload.product_ids)
        .gte("release_report_week_start", str(week_start))
        .execute()
    )

    updated = 0
    for row in pub_resp.data or []:
        supabase.schema("preorder").from_("release_state").update(
            {"nyt_uploaded_at": now_iso}
        ).eq("product_id", row["product_id"]).eq(
            "effective_pub_date", row["effective_pub_date"]
        ).execute()
        updated += 1

    # Flip the log row to success if it's currently fallback
    supabase.schema("preorder").from_("nyt_report_log").update(
        {"upload_status": "success", "uploaded_at": now_iso}
    ).eq("week_start", str(week_start)).eq("upload_status", "fallback").execute()

    logger.info(f"Manual upload confirmed — {updated} titles marked for week {week_start}")
    return {
        "marked":     updated,
        "week_start": str(week_start),
        "week_end":   str(week_end),
    }