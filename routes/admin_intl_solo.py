"""
Admin intl-solo Routes
======================
Read-only endpoints for international cart isolation (PR 1). Write endpoints
(clear, restore, force-on, reconcile) arrive in PR 2.

See services/intl_solo_service.py and
db/migrations/2026_11_create_intl_solo_state.sql.

Endpoints (mounted under /admin/preorders):
  GET /intl-solo        — derived state per product, plus summary counts
                          (?include_not_applicable=true to list every product)
  GET /intl-solo/log    — audit log, newest first (?product_id=, ?limit=)

Auth: X-Admin-Token, matching the other admin_* routers. Uses the lazy
Supabase client (docs/DOCS_STATUS.md Landmine 10).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from dependencies import get_supabase_client
from services import intl_solo_service
from services.hold_service import today_et

logger = logging.getLogger(__name__)

router = APIRouter()

ADMIN_TOKEN = os.getenv("PREORDER_ADMIN_TOKEN")


def require_admin_token(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True


@router.get("/intl-solo")
def list_intl_solo(
    include_not_applicable: bool = False,
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
):
    as_of = today_et()
    states = intl_solo_service.list_states(
        supabase, include_not_applicable=include_not_applicable, as_of=as_of
    )
    return {
        "today_et": as_of.isoformat(),
        "tag": intl_solo_service.TAG,
        "count": len(states),
        "summary": intl_solo_service.summarize(states),
        "products": states,
    }


@router.get("/intl-solo/log")
def intl_solo_log(
    product_id: Optional[int] = None,
    limit: int = Query(default=50, ge=1, le=200),
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
):
    entries = intl_solo_service.list_log(supabase, product_id=product_id, limit=limit)
    return {"count": len(entries), "entries": entries}
