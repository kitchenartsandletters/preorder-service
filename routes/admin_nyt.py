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
from fastapi.responses import Response
from pydantic import BaseModel
from supabase import create_client, Client
from fastapi.responses import PlainTextResponse
from jobs.nyt_reporter import run as run_reporter
from jobs.nyt_reporter import (
    _fetch_queued_titles,
    _fetch_presale_qtys,
    _fetch_product_metadata,
    _fetch_shopify_week_sales as _nyt_fetch_shopify_week_sales,
    _generate_csv,
)

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
    if not payload.product_ids and not payload.week_anchor:
        raise HTTPException(status_code=422, detail="product_ids or week_anchor required")

    if payload.week_anchor:
        anchor = date.fromisoformat(payload.week_anchor)
        days_since_sunday = anchor.isoweekday() % 7
        week_start = anchor - timedelta(days=days_since_sunday)
        week_end   = week_start + timedelta(days=6)
    else:
        week_start, week_end = _current_week_bounds()

    now_iso = datetime.now(UTC).isoformat()

    # If no product_ids provided, fetch all queued titles for this week
    product_ids = payload.product_ids
    if not product_ids:
        queued_resp = (
            supabase.schema("preorder")
            .from_("release_state")
            .select("product_id, effective_pub_date")
            .eq("released_to_reporting", True)
            .is_("nyt_uploaded_at", "null")
            .gte("release_report_week_start", str(week_start))
            .lte("release_report_week_end", str(week_end))
            .execute()
        )
        rows_to_mark = queued_resp.data or []
    else:
        pub_resp = (
            supabase.schema("preorder")
            .from_("release_state")
            .select("product_id, effective_pub_date")
            .in_("product_id", product_ids)
            .gte("release_report_week_start", str(week_start))
            .execute()
        )
        rows_to_mark = pub_resp.data or []

    updated = 0
    for row in rows_to_mark:
        supabase.schema("preorder").from_("release_state").update(
            {"nyt_uploaded_at": now_iso}
        ).eq("product_id", row["product_id"]).eq(
            "effective_pub_date", row["effective_pub_date"]
        ).execute()
        updated += 1

    # Flip the log row to success
    supabase.schema("preorder").from_("nyt_report_log").update(
        {"upload_status": "success", "uploaded_at": now_iso}
    ).eq("week_start", str(week_start)).in_(
        "upload_status", ["fallback", "error"]
    ).execute()

    logger.info(f"Manual upload confirmed — {updated} titles marked for week {week_start}")
    return {
        "marked":     updated,
        "week_start": str(week_start),
        "week_end":   str(week_end),
    }

@router.post("/nyt/regenerate")
def regenerate_nyt_report(
    payload: dict,
    ok: bool = Depends(require_admin_token)
):
    print("[regen] endpoint entered", flush=True)

    week_anchor = payload.get("week_anchor")
    if not week_anchor:
        raise HTTPException(status_code=422, detail="week_anchor required")

    # week_anchor IS the sales week — compute sales bounds directly
    anchor = date.fromisoformat(week_anchor)
    days_since_sunday = anchor.isoweekday() % 7
    sales_start = anchor - timedelta(days=days_since_sunday)
    sales_end   = sales_start + timedelta(days=6)

    # Queue week is the following week
    queue_start = sales_start + timedelta(days=7)
    queue_end   = sales_end + timedelta(days=7)

    print(f"[regen] queue={queue_start}→{queue_end} sales={sales_start}→{sales_end}", flush=True)

    queued = _fetch_queued_titles(supabase, queue_start, queue_end)
    print(f"[regen] queued={len(queued)}", flush=True)

    product_ids = [int(r["product_id"]) for r in queued]
    presales = _fetch_presale_qtys(supabase, product_ids) if product_ids else {}
    week_sales = _nyt_fetch_shopify_week_sales(sales_start, sales_end)
    print(f"[regen] shopify products={len(week_sales)} units={sum(week_sales.values())}", flush=True)

    metadata = _fetch_product_metadata(supabase, product_ids) if product_ids else {}
    csv_text, csv_filename, row_count = _generate_csv(
        queued, presales, week_sales, metadata, sales_end, supabase
    )
    print(f"[regen] csv rows={row_count}", flush=True)

    now_utc = datetime.utcnow().isoformat() + "Z"
    supabase.schema("preorder").table("nyt_report_log").upsert(
    {
        "week_start": str(sales_start),   # ← sales week, not queue week
        "week_end":   str(sales_end),     # ← sales week
        "csv_filename": csv_filename,
        "csv_content":  csv_text,
        "titles_count": row_count,
        "upload_status": "fallback",
        "fallback_reason": f"Regenerated {now_utc} — original run had incorrect data",
        "uploaded_at": None,
        "created_at":  now_utc,
    },
    on_conflict="week_start,week_end",
    ).execute()

    return {
        "week_start": str(sales_start),
        "week_end":   str(sales_end),
        "row_count":  row_count,
        "csv_filename": csv_filename,
    }