"""
Admin Cleanup Routes
====================
API endpoints for Workstream 1: Preorder Product Cleanup.

Provides on-demand, admin-controlled operations that replace the
automated preorderManager.py cron from the legacy repo.

All endpoints:
- Require X-Admin-Token authentication
- Log actions to preorder.cleanup_log in Supabase
- Return structured responses with operation results

Endpoints:
  GET  /cleanup/state/{product_id}     — Fetch cleanup state from Shopify
  POST /cleanup/description/{product_id} — Clean Body HTML (remove preamble/footer)
  POST /cleanup/collection/{product_id}  — Remove from Preorder collection
  POST /cleanup/unpublish/{product_id}   — Unpublish from Catch All channel
  POST /cleanup/full/{product_id}        — Run all three cleanup steps

Delayed-import hold: /cleanup/collection, /cleanup/unpublish and /cleanup/full
refuse with 409 when the title is a delayed import (pub date passed, stock never
arrived, still deliberately an active preorder). Pass ?force=true to override;
forced runs are logged with forced=True. /cleanup/description is not guarded —
it only rewrites marketing copy and changes no status. See
services/cleanup_guard.py.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from supabase import create_client, Client

from dotenv import load_dotenv

load_dotenv()

from shopify_client import ShopifyClient
from services.cleanup_guard import check_delayed_import_hold
from services.shopify_cleanup import (
    fetch_cleanup_state,
    analyze_description,
    clean_description,
    remove_from_preorder_collection,
    unpublish_from_catch_all,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ──────────────────────────────────────────────
# Environment + clients
# ──────────────────────────────────────────────

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ADMIN_TOKEN = os.getenv("PREORDER_ADMIN_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# ──────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────

def require_admin_token(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True


# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

def log_cleanup_action(
    product_id: int,
    action: str,
    success: bool,
    details: Optional[Dict[str, Any]] = None,
    errors: Optional[List[Dict]] = None,
) -> None:
    """
    Write a row to preorder.cleanup_log for audit trail.
    """
    try:
        supabase.schema("preorder").table("cleanup_log").insert({
            "product_id": product_id,
            "action": action,
            "success": success,
            "details": details or {},
            "errors": errors or [],
            "performed_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.error(f"Failed to log cleanup action: {e}")
        print(f"❌ Cleanup log insert failed: {e}")


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

def _refuse_if_held(product_id: int, force: bool) -> Optional[Dict[str, Any]]:
    """
    Raise 409 when the product is a delayed-import hold and force is not set.
    Returns the hold (or None) so callers can mark a forced run in the log.
    """
    hold = check_delayed_import_hold(supabase, product_id)
    if hold and not force:
        raise HTTPException(status_code=409, detail=hold)
    return hold


@router.get("/cleanup/state/{product_id}")
async def get_cleanup_state(product_id: int, ok: bool = Depends(require_admin_token)):
    """
    Fetch the current cleanup state for a product directly from Shopify.
    Returns description analysis, collection membership, and publication status.
    """
    client = ShopifyClient()
    try:
        state = await fetch_cleanup_state(client, product_id)
        return state.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to fetch cleanup state for {product_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.post("/cleanup/description/{product_id}")
async def cleanup_description(product_id: int, ok: bool = Depends(require_admin_token)):
    """
    Remove preorder preamble and footer from a product's Body HTML.
    Fetches current state, analyzes, and applies cleaned version.
    No-ops if description is already clean.
    """
    client = ShopifyClient()
    try:
        state = await fetch_cleanup_state(client, product_id)

        if not state.description_analysis.needs_cleaning:
            return {
                "product_id": product_id,
                "action": "clean_description",
                "result": "no_change",
                "description_status": state.description_analysis.status,
                "message": "Description is already clean.",
            }

        errors = await clean_description(
            client, product_id, state.description_analysis.cleaned_html
        )

        success = len(errors) == 0
        log_cleanup_action(
            product_id=product_id,
            action="clean_description",
            success=success,
            details={
                "title": state.title,
                "previous_status": state.description_analysis.status,
                "had_preamble": state.description_analysis.has_preamble,
                "had_footer": state.description_analysis.has_footer,
            },
            errors=errors if errors else None,
        )

        if not success:
            raise HTTPException(status_code=500, detail={
                "message": "Shopify returned errors",
                "errors": errors,
            })

        return {
            "product_id": product_id,
            "action": "clean_description",
            "result": "cleaned",
            "previous_status": state.description_analysis.status,
            "message": f"Description cleaned for '{state.title}'.",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to clean description for {product_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.post("/cleanup/collection/{product_id}")
async def cleanup_collection(
    product_id: int, force: bool = False, ok: bool = Depends(require_admin_token)
):
    """
    Remove a product from the Preorder collection.
    No-ops if already removed.

    Refuses with 409 for a delayed-import hold unless force=true.
    """
    hold = _refuse_if_held(product_id, force)
    client = ShopifyClient()
    try:
        state = await fetch_cleanup_state(client, product_id)

        if not state.in_preorder_collection:
            return {
                "product_id": product_id,
                "action": "remove_from_collection",
                "result": "no_change",
                "message": "Product is not in the Preorder collection.",
            }

        errors = await remove_from_preorder_collection(client, product_id)

        success = len(errors) == 0
        log_cleanup_action(
            product_id=product_id,
            action="remove_from_collection",
            success=success,
            details={
                "title": state.title,
                "collection_gid": state.preorder_collection_gid,
                "forced": bool(hold),
            },
            errors=errors if errors else None,
        )

        if not success:
            raise HTTPException(status_code=500, detail={
                "message": "Shopify returned errors",
                "errors": errors,
            })

        return {
            "product_id": product_id,
            "action": "remove_from_collection",
            "result": "removed",
            "message": f"'{state.title}' removed from Preorder collection.",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove {product_id} from collection: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.post("/cleanup/unpublish/{product_id}")
async def cleanup_unpublish(
    product_id: int, force: bool = False, ok: bool = Depends(require_admin_token)
):
    """
    Unpublish a product from the Catch All publication channel.
    No-ops if already unpublished.

    Refuses with 409 for a delayed-import hold unless force=true: the title is
    still selling as a preorder to customers awaiting stock.
    """
    hold = _refuse_if_held(product_id, force)
    client = ShopifyClient()
    try:
        state = await fetch_cleanup_state(client, product_id)

        if not state.published_to_catch_all:
            return {
                "product_id": product_id,
                "action": "unpublish_catch_all",
                "result": "no_change",
                "message": "Product is not published to Catch All.",
            }

        errors = await unpublish_from_catch_all(client, product_id)

        success = len(errors) == 0
        log_cleanup_action(
            product_id=product_id,
            action="unpublish_catch_all",
            success=success,
            details={
                "title": state.title,
                "forced": bool(hold),
            },
            errors=errors if errors else None,
        )

        if not success:
            raise HTTPException(status_code=500, detail={
                "message": "Shopify returned errors",
                "errors": errors,
            })

        return {
            "product_id": product_id,
            "action": "unpublish_catch_all",
            "result": "unpublished",
            "message": f"'{state.title}' unpublished from Catch All.",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to unpublish {product_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.post("/cleanup/full/{product_id}")
async def cleanup_full(
    product_id: int, force: bool = False, ok: bool = Depends(require_admin_token)
):
    """
    Run the full cleanup sequence for a product:
    1. Clean Body HTML (remove preamble/footer)
    2. Remove from Preorder collection
    3. Unpublish from Catch All channel

    Each step is independent — if one fails, the others still execute.
    Returns per-step results.

    Refuses with 409 for a delayed-import hold unless force=true. The refusal
    covers the whole sequence: full cleanup means "retire this title", which is
    exactly what must not happen while the hold stands.
    """
    hold = _refuse_if_held(product_id, force)
    client = ShopifyClient()
    try:
        state = await fetch_cleanup_state(client, product_id)
        results = {
            "product_id": product_id,
            "title": state.title,
            "action": "full_cleanup",
            "steps": {},
        }

        # Step 1: Clean description
        if state.description_analysis.needs_cleaning:
            try:
                errors = await clean_description(
                    client, product_id, state.description_analysis.cleaned_html
                )
                success = len(errors) == 0
                results["steps"]["clean_description"] = {
                    "result": "cleaned" if success else "failed",
                    "previous_status": state.description_analysis.status,
                    "errors": errors if errors else None,
                }
                log_cleanup_action(
                    product_id=product_id,
                    action="clean_description",
                    success=success,
                    details={"title": state.title, "source": "full_cleanup", "forced": bool(hold)},
                    errors=errors if errors else None,
                )
            except Exception as e:
                results["steps"]["clean_description"] = {
                    "result": "error",
                    "message": str(e),
                }
        else:
            results["steps"]["clean_description"] = {"result": "no_change"}

        # Step 2: Remove from Preorder collection
        if state.in_preorder_collection:
            try:
                errors = await remove_from_preorder_collection(client, product_id)
                success = len(errors) == 0
                results["steps"]["remove_from_collection"] = {
                    "result": "removed" if success else "failed",
                    "errors": errors if errors else None,
                }
                log_cleanup_action(
                    product_id=product_id,
                    action="remove_from_collection",
                    success=success,
                    details={"title": state.title, "source": "full_cleanup", "forced": bool(hold)},
                    errors=errors if errors else None,
                )
            except Exception as e:
                results["steps"]["remove_from_collection"] = {
                    "result": "error",
                    "message": str(e),
                }
        else:
            results["steps"]["remove_from_collection"] = {"result": "no_change"}

        # Step 3: Unpublish from Catch All
        if state.published_to_catch_all:
            try:
                errors = await unpublish_from_catch_all(client, product_id)
                success = len(errors) == 0
                results["steps"]["unpublish_catch_all"] = {
                    "result": "unpublished" if success else "failed",
                    "errors": errors if errors else None,
                }
                log_cleanup_action(
                    product_id=product_id,
                    action="unpublish_catch_all",
                    success=success,
                    details={"title": state.title, "source": "full_cleanup", "forced": bool(hold)},
                    errors=errors if errors else None,
                )
            except Exception as e:
                results["steps"]["unpublish_catch_all"] = {
                    "result": "error",
                    "message": str(e),
                }
        else:
            results["steps"]["unpublish_catch_all"] = {"result": "no_change"}

        # Overall success
        step_results = [s.get("result") for s in results["steps"].values()]
        results["overall"] = "success" if "failed" not in step_results and "error" not in step_results else "partial"

        return results

    except Exception as e:
        logger.error(f"Full cleanup failed for {product_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()