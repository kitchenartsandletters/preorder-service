"""
Admin intl-solo Routes
======================
Endpoints for international cart isolation. See docs/intl_solo.md,
services/intl_solo_service.py (read side) and services/intl_solo_actions.py
(write side).

Endpoints (mounted under /admin/preorders):
  GET  /intl-solo                       — derived state per product, plus summary counts
                                          (?include_not_applicable=true to list every product)
  GET  /intl-solo/log                   — audit log, newest first (?product_id=, ?limit=)
  POST /intl-solo/{product_id}/clear    — clear on basis received | guaranteed (reason required)
  POST /intl-solo/{product_id}/restore  — undo a clearance (reason required)
  POST /intl-solo/{product_id}/force-on — restrict a non-preorder (reason required)
  POST /intl-solo/reconcile             — dry-run by default; apply needs confirm="apply"

Errors: 400 bad input, 404 unknown product, 409 action not allowed in the
title's current state, 502 Shopify refused a tag write (the response says
which side, if any, was recorded).

Auth: X-Admin-Token, matching the other admin_* routers. Uses the lazy
Supabase client (docs/DOCS_STATUS.md Landmine 10).
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from dependencies import get_shopify_client, get_supabase_client
from services import intl_solo_actions as actions
from services import intl_solo_service
from services.hold_service import today_et

logger = logging.getLogger(__name__)

router = APIRouter()

ADMIN_TOKEN = os.getenv("PREORDER_ADMIN_TOKEN")


def require_admin_token(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True


class ClearRequest(BaseModel):
    basis: str = Field(..., description="received | guaranteed")
    reason: str = Field(..., description="Why the restriction can be lifted")
    changed_by: Optional[str] = Field(None, description="Who made the decision")


class ReasonRequest(BaseModel):
    reason: str = Field(..., description="Why")
    changed_by: Optional[str] = Field(None, description="Who made the decision")


class ReconcileRequest(BaseModel):
    apply: bool = Field(False, description="False = dry run (default)")
    confirm: Optional[str] = Field(None, description='Must be "apply" when apply is true')
    product_ids: Optional[List[int]] = Field(None, description="Limit to these products")
    changed_by: Optional[str] = None


def _http_error(exc: Exception):
    if isinstance(exc, actions.IntlSoloActionError):
        return HTTPException(status_code=409, detail={
            "message": str(exc), "display_state": exc.display_state})
    if isinstance(exc, actions.IntlSoloShopifyError):
        return HTTPException(status_code=502, detail={
            "message": str(exc), "errors": exc.errors})
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return None


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


@router.post("/intl-solo/reconcile")
async def intl_solo_reconcile(
    payload: ReconcileRequest,
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
    client=Depends(get_shopify_client),
):
    try:
        return await actions.reconcile(
            supabase, client, apply=payload.apply, confirm=payload.confirm,
            product_ids=payload.product_ids, changed_by=payload.changed_by,
        )
    except Exception as exc:
        http = _http_error(exc)
        if http:
            raise http
        logger.exception("[intl-solo] reconcile failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/intl-solo/{product_id}/clear")
async def intl_solo_clear(
    product_id: int,
    payload: ClearRequest,
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
    client=Depends(get_shopify_client),
):
    try:
        return await actions.clear(supabase, client, product_id, payload.basis,
                                   payload.reason, payload.changed_by)
    except Exception as exc:
        http = _http_error(exc)
        if http:
            raise http
        logger.exception(f"[intl-solo] clear failed for {product_id}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/intl-solo/{product_id}/restore")
async def intl_solo_restore(
    product_id: int,
    payload: ReasonRequest,
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
    client=Depends(get_shopify_client),
):
    try:
        return await actions.restore(supabase, client, product_id,
                                     payload.reason, payload.changed_by)
    except Exception as exc:
        http = _http_error(exc)
        if http:
            raise http
        logger.exception(f"[intl-solo] restore failed for {product_id}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/intl-solo/{product_id}/force-on")
async def intl_solo_force_on(
    product_id: int,
    payload: ReasonRequest,
    ok: bool = Depends(require_admin_token),
    supabase=Depends(get_supabase_client),
    client=Depends(get_shopify_client),
):
    try:
        return await actions.force_on(supabase, client, product_id,
                                      payload.reason, payload.changed_by)
    except Exception as exc:
        http = _http_error(exc)
        if http:
            raise http
        logger.exception(f"[intl-solo] force_on failed for {product_id}")
        raise HTTPException(status_code=500, detail=str(exc))
