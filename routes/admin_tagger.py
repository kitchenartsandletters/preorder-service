"""
Admin Tagger Routes
===================
API endpoints for Workstream 2: Order Tagging status.

Endpoints:
  GET  /tagger/runs              — Recent run log entries (newest first)
  GET  /tagger/runs/latest       — Single most recent completed run
  GET  /tagger/runs/{run_id}/orders — Orders tagged in a specific run
  GET  /tagger/stats             — Aggregate stats (7d, 30d)
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from supabase import create_client, Client

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

# ──────────────────────────────────────────────
# Environment + clients
# ──────────────────────────────────────────────

SUPABASE_URL             = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ADMIN_TOKEN              = os.getenv("PREORDER_ADMIN_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# ──────────────────────────────────────────────
# Auth (same as all other admin routes)
# ──────────────────────────────────────────────

def require_admin_token(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@router.get("/tagger/runs")
def list_runs(
    limit: int = Query(default=25, le=100),
    ok: bool = Depends(require_admin_token),
):
    """Recent tagger run log entries, newest first."""
    result = (
        supabase
        .schema("preorder")
        .from_("tagger_run_log")
        .select(
            "id, started_at, completed_at, status, "
            "orders_fetched, orders_skipped, orders_tagged, "
            "preorder_count, mixed_count, errors, tagger_version"
        )
        .order("started_at", desc=True)
        .limit(limit)
        .execute()
    )
    return {"runs": result.data}


@router.get("/tagger/runs/latest")
def latest_run(ok: bool = Depends(require_admin_token)):
    """Most recent completed run. Used for the AD status header."""
    result = (
        supabase
        .schema("preorder")
        .from_("tagger_run_log")
        .select("*")
        .in_("status", ["success", "partial", "error"])
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return {"run": None}
    return {"run": result.data[0]}


@router.get("/tagger/runs/{run_id}/orders")
def run_orders(
    run_id: str,
    limit: int = Query(default=50, le=200),
    ok: bool = Depends(require_admin_token),
):
    """Orders tagged in a specific run."""
    result = (
        supabase
        .schema("preorder")
        .from_("tagger_processed_orders")
        .select("id, order_gid, order_name, tags_applied, processed_at")
        .eq("run_id", run_id)
        .order("processed_at", desc=True)
        .limit(limit)
        .execute()
    )
    return {"orders": result.data}


@router.get("/tagger/stats")
def tagger_stats(ok: bool = Depends(require_admin_token)):
    """Aggregate tag counts for 7-day and 30-day windows."""
    total = (
        supabase
        .schema("preorder")
        .from_("tagger_processed_orders")
        .select("id", count="exact")
        .execute()
    )

    def sum_window(days: int):
        rows = (
            supabase
            .schema("preorder")
            .from_("tagger_run_log")
            .select("orders_tagged, preorder_count, mixed_count")
            .in_("status", ["success", "partial"])
            .gte("started_at", f"now() - interval '{days} days'")
            .execute()
        )
        return {
            "orders_tagged":  sum(r["orders_tagged"]  for r in rows.data),
            "preorder_count": sum(r["preorder_count"] for r in rows.data),
            "mixed_count":    sum(r["mixed_count"]    for r in rows.data),
        }

    return {
        "total_processed": total.count,
        "last_7_days":     sum_window(7),
        "last_30_days":    sum_window(30),
    }