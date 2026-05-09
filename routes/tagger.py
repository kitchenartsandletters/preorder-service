"""
routers/tagger.py — Tagger status endpoints for the Admin Dashboard
Add to preorder-service FastAPI app:

    from routers import tagger
    app.include_router(tagger.router)
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import APIKeyHeader
from supabase import Client, create_client

router = APIRouter(prefix="/tagger", tags=["tagger"])

# ── Auth (same pattern as rest of preorder-service) ───────────────────────────
ADMIN_TOKEN = os.environ["PREORDER_ADMIN_TOKEN"]
api_key_header = APIKeyHeader(name="X-Admin-Token", auto_error=True)


def verify_token(token: str = Depends(api_key_header)):
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")


# ── Supabase ──────────────────────────────────────────────────────────────────
def get_supabase() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/runs", dependencies=[Depends(verify_token)])
async def list_runs(limit: int = Query(default=25, le=100)):
    """
    Return the most recent tagger run log entries, newest first.
    Used by the AD Order Tagging status page.
    """
    sb = get_supabase()
    result = (
        sb.schema("preorder")
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


@router.get("/runs/latest", dependencies=[Depends(verify_token)])
async def latest_run():
    """Return the single most recent completed run. Used for the status header."""
    sb = get_supabase()
    result = (
        sb.schema("preorder")
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


@router.get("/runs/{run_id}/orders", dependencies=[Depends(verify_token)])
async def run_orders(run_id: str, limit: int = Query(default=50, le=200)):
    """Return the orders processed in a specific run."""
    sb = get_supabase()
    result = (
        sb.schema("preorder")
        .from_("tagger_processed_orders")
        .select("id, order_gid, order_name, tags_applied, processed_at")
        .eq("run_id", run_id)
        .order("processed_at", desc=True)
        .limit(limit)
        .execute()
    )
    return {"orders": result.data}


@router.get("/stats", dependencies=[Depends(verify_token)])
async def tagger_stats():
    """Aggregate stats: total orders tagged all-time, last 7 days, last 30 days."""
    sb = get_supabase()

    total = (
        sb.schema("preorder")
        .from_("tagger_processed_orders")
        .select("id", count="exact")
        .execute()
    )

    result_7d = (
        sb.schema("preorder")
        .from_("tagger_run_log")
        .select("orders_tagged, preorder_count, mixed_count")
        .in_("status", ["success", "partial"])
        .gte("started_at", "now() - interval '7 days'")
        .execute()
    )

    result_30d = (
        sb.schema("preorder")
        .from_("tagger_run_log")
        .select("orders_tagged, preorder_count, mixed_count")
        .in_("status", ["success", "partial"])
        .gte("started_at", "now() - interval '30 days'")
        .execute()
    )

    def sum_runs(runs):
        return {
            "orders_tagged":  sum(r["orders_tagged"]  for r in runs),
            "preorder_count": sum(r["preorder_count"] for r in runs),
            "mixed_count":    sum(r["mixed_count"]    for r in runs),
        }

    return {
        "total_processed": total.count,
        "last_7_days":  sum_runs(result_7d.data),
        "last_30_days": sum_runs(result_30d.data),
    }