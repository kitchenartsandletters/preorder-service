"""
Admin Shipping Profile Routes
==============================
API endpoints for Workstream 3: Shipping Profile Management.

Provides visibility and control over delivery profiles used for
per-pub-date shipping cost calculation on preorder products.

Endpoints:
  GET  /shipping/profiles                           — List all profiles with products
  GET  /shipping/profiles/{profile_id}              — Get profile detail
  GET  /shipping/profiles/by-date/{pub_date}        — Find profile matching a pub date
  GET  /shipping/profiles/reference-preview         — Preview participant config the builder would clone (read-only)
  POST /shipping/profiles/assign/{product_id}       — Assign product to date-matched profile
  POST /shipping/profiles/create-direct             — Create a profile directly from the reference template (bypasses pipeline)
  POST /shipping/profiles/remove/{product_id}       — Remove product from its profile
  GET  /shipping/profiles/week-plan                 — Read-only week-grouping plan (Phase 2 preview)
  POST /shipping/profiles/week-apply                — Apply the week plan for one release week
  POST /shipping/profiles/reconcile                 — Reconcile all active preorders with profiles
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from supabase import create_client, Client
from pydantic import BaseModel

from dotenv import load_dotenv

load_dotenv()

from shopify_client import ShopifyClient
from services.shipping_profiles import (
    list_shipping_profiles,
    get_profile_detail,
    find_profile_for_date,
    find_or_create_profile_for_date,
    assign_product_to_profile,
    remove_product_from_profile,
    pub_date_to_profile_name,
    create_profile_from_template,
    preview_reference_clone,
)
from services.week_migration import build_week_plan, apply_week_plan

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
# Request models
# ──────────────────────────────────────────────

class AssignRequest(BaseModel):
    pub_date: str  # YYYY-MM-DD
    variant_gid: Optional[str] = None  # If not provided, assigns all variants of the product


class CreateProfileRequest(BaseModel):
    name: str
    variant_gid: str
    reference_gid: Optional[str] = None  # override; defaults to SHIPPING_REFERENCE_PROFILE_GID


class WeekApplyRequest(BaseModel):
    week_start: str  # YYYY-MM-DD, must be a Sunday


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@router.get("/shipping/profiles")
async def list_profiles(ok: bool = Depends(require_admin_token)):
    """
    List all delivery profiles with their assigned products.
    Excludes the General (default) profile from the list.
    """
    client = ShopifyClient()
    try:
        profiles = await list_shipping_profiles(client)
        # Separate default from date-based
        date_profiles = [p for p in profiles if not p["is_default"]]
        default_profile = next((p for p in profiles if p["is_default"]), None)

        return {
            "profiles": date_profiles,
            "default_profile": default_profile,
            "total_date_profiles": len(date_profiles),
            "total_products_on_profiles": sum(p["product_count"] for p in date_profiles),
        }
    except Exception as e:
        logger.error(f"Failed to list shipping profiles: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.get("/shipping/profiles/by-date/{pub_date}")
async def get_profile_by_date(pub_date: str, ok: bool = Depends(require_admin_token)):
    """
    Find a delivery profile matching a specific pub date.
    pub_date format: YYYY-MM-DD
    """
    try:
        parsed_date = datetime.strptime(pub_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    client = ShopifyClient()
    try:
        profile = await find_profile_for_date(client, parsed_date)
        if not profile:
            return {
                "found": False,
                "pub_date": pub_date,
                "expected_name": pub_date_to_profile_name(parsed_date),
                "message": "No profile found for this pub date.",
            }
        return {
            "found": True,
            "pub_date": pub_date,
            "profile": profile,
        }
    except Exception as e:
        logger.error(f"Failed to find profile for date {pub_date}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.get("/shipping/profiles/reference-preview")
async def reference_preview(ok: bool = Depends(require_admin_token)):
    """
    Read-only preview of the carrier participant config that
    create_profile_from_template would clone from SHIPPING_REFERENCE_PROFILE_GID.

    Creates nothing. Doubles as a pre-flight check: if the reference is unset,
    missing, or has an unserviced carrier, this returns 400 (the same guard the
    create path enforces) instead of a clone. Declared before the
    /{profile_id} route so the static path is not parsed as an id.
    """
    client = ShopifyClient()
    try:
        preview = await preview_reference_clone(client)
        zones = preview["zones"]
        would_clone = {
            zone: [
                {
                    "method": m["name"],
                    "carrier": m["participant"]["carrierServiceId"],
                    "active_services": [
                        s["name"]
                        for s in m["participant"].get("participantServices", [])
                        if s.get("active")
                    ],
                    "fixed_fee": m["participant"].get("fixedFee"),
                    "percentage_of_rate_fee": m["participant"].get("percentageOfRateFee"),
                    "adapt_to_new_services": m["participant"].get("adaptToNewServicesFlag", False),
                }
                for m in methods
            ]
            for zone, methods in zones.items()
        }
        return {
            "reference_profile_gid": preview["reference_profile_gid"],
            "would_clone": would_clone,
            "raw_zone_methods": zones,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to preview reference clone: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.get("/shipping/profiles/week-plan")
async def week_plan(ok: bool = Depends(require_admin_token)):
    """
    Read-only Phase 2 preview: the week-based grouping plan for active
    preorders — which week profiles are needed, which titles move where, which
    per-date profiles would empty out. Creates nothing. Declared before the
    /{profile_id} route so the static path is not parsed as an id.
    """
    client = ShopifyClient()
    try:
        return await build_week_plan(client, supabase)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to build week plan: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.get("/shipping/profiles/{profile_id}")
async def get_profile(profile_id: int, ok: bool = Depends(require_admin_token)):
    """Get detailed information about a specific delivery profile."""
    profile_gid = f"gid://shopify/DeliveryProfile/{profile_id}"
    client = ShopifyClient()
    try:
        profile = await get_profile_detail(client, profile_gid)
        return profile
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.post("/shipping/profiles/assign/{product_id}")
async def assign_to_profile(
    product_id: int,
    request: AssignRequest,
    ok: bool = Depends(require_admin_token),
):
    """
    Assign a product to the delivery profile matching the given pub date.
    If no profile exists for the date, attempts to repurpose an empty
    historical profile by renaming it.
    """
    try:
        parsed_date = datetime.strptime(request.pub_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    client = ShopifyClient()
    try:
        # Find or create profile for this date
        profile = await find_or_create_profile_for_date(client, parsed_date, product_id, variant_gid=request.variant_gid)

        # Assign product
        errors = await assign_product_to_profile(client, profile["profile_gid"], product_id, variant_gid=request.variant_gid,)

        if errors:
            raise HTTPException(status_code=500, detail={
                "message": "Failed to assign product to profile",
                "errors": errors,
            })

        return {
            "product_id": product_id,
            "action": "assigned",
            "profile_name": profile["name"],
            "profile_gid": profile["profile_gid"],
            "pub_date": request.pub_date,
            "message": f"Product {product_id} assigned to '{profile['name']}' profile.",
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to assign product {product_id} to profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.post("/shipping/profiles/create-direct")
async def create_profile_direct(
    request: CreateProfileRequest,
    ok: bool = Depends(require_admin_token),
):
    """
    Create a delivery profile directly from the reference template, attaching
    the given variant. Bypasses the preorder pipeline (no pub-date / find-or-
    create logic) — used to provision a profile by hand and to verify the
    builder in isolation with a throwaway product's variant. Carrier participant
    config is cloned from SHIPPING_REFERENCE_PROFILE_GID (or the request
    override). Raises 400 if the reference is unset or would clone an unserviced
    carrier — nothing is created in that case.
    """
    if not request.name or not request.variant_gid:
        raise HTTPException(status_code=400, detail="name and variant_gid are required")

    client = ShopifyClient()
    try:
        created = await create_profile_from_template(
            client,
            request.name,
            request.variant_gid,
            reference_gid=request.reference_gid,
        )
        return {
            "action": "created",
            "profile": created,
            "message": f"Created profile '{created['name']}' ({created['profile_gid']}).",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create profile directly: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.post("/shipping/profiles/week-apply")
async def week_apply(request: WeekApplyRequest, ok: bool = Depends(require_admin_token)):
    """
    Apply the week plan for a SINGLE release week (week_start = a Sunday,
    YYYY-MM-DD). Ensures the week profile exists (created on the verified
    builder if needed) and assigns every title in that week to it. Preflights
    the reference clone before any create. Scoped to one week so the caller can
    rate-check before applying more.
    """
    try:
        ws = datetime.strptime(request.week_start, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid week_start. Use YYYY-MM-DD (a Sunday).")
    if ws.isoweekday() != 7:
        raise HTTPException(status_code=400, detail=f"week_start must be a Sunday; {request.week_start} is not.")

    client = ShopifyClient()
    try:
        return await apply_week_plan(client, supabase, ws)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to apply week plan for {request.week_start}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.post("/shipping/profiles/remove/{product_id}")
async def remove_from_profile(product_id: int, ok: bool = Depends(require_admin_token)):
    """
    Remove a product from its current delivery profile.
    The product falls back to the General (default) profile.
    Finds the product's current profile automatically.
    """
    client = ShopifyClient()
    try:
        # Find which profile this product is on
        profiles = await list_shipping_profiles(client)
        current_profile = None
        for profile in profiles:
            if profile["is_default"]:
                continue
            for prod in profile["products"]:
                if prod["product_id"] == product_id:
                    current_profile = profile
                    break
            if current_profile:
                break

        if not current_profile:
            return {
                "product_id": product_id,
                "action": "no_change",
                "message": "Product is not on any date-based shipping profile (already on General).",
            }

        # Get variant GIDs for dissociation
        errors = await remove_product_from_profile(
            client, current_profile["profile_gid"], product_id
        )

        if errors:
            raise HTTPException(status_code=500, detail={
                "message": "Failed to remove product from profile",
                "errors": errors,
            })

        return {
            "product_id": product_id,
            "action": "removed",
            "previous_profile": current_profile["name"],
            "message": f"Product {product_id} removed from '{current_profile['name']}'. Now on General profile.",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove product {product_id} from profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.post("/shipping/profiles/reconcile")
async def reconcile_profiles(ok: bool = Depends(require_admin_token)):
    """
    Reconcile all active preorder products with their expected shipping profiles.
    
    For each active/early_stock preorder product with a pub_date:
    - Check if the product is on the correct date-based profile
    - Report mismatches (wrong profile, missing from profile, should be on General)
    - Early stock arrivals with inventory > 0 are marked as exempt
    
    Does NOT make changes — returns a report of what needs fixing.
    """
    client = ShopifyClient()
    try:
        # Get all profiles
        profiles = await list_shipping_profiles(client)

        # Build product → profile lookup
        product_profile_map: Dict[int, Dict] = {}
        for profile in profiles:
            if profile["is_default"]:
                continue
            for prod in profile["products"]:
                if prod["product_id"]:
                    product_profile_map[prod["product_id"]] = {
                        "profile_name": profile["name"],
                        "profile_gid": profile["profile_gid"],
                        "profile_pub_date": profile["pub_date"],
                    }

        # Get active preorder products from Supabase — include title and inventory
        response = (
            supabase.schema("preorder")
            .table("product_status")
            .select("product_id, status, effective_pub_date, metadata_snapshot")
            .in_("status", ["active_preorder", "early_stock_arrival"])
            .execute()
        )

        today = date.today()
        report = {
            "correctly_assigned": [],
            "wrong_profile": [],
            "missing_from_profile": [],
            "should_be_removed": [],
            "exempt": [],
            "no_pub_date": [],
        }

        for row in response.data:
            pid = row["product_id"]
            status = row["status"]
            pub_date_str = row.get("effective_pub_date")
            meta = row.get("metadata_snapshot") or {}
            title = meta.get("title", f"Product {pid}")
            inventory = int(meta.get("inventory", 0))

            if not pub_date_str:
                report["no_pub_date"].append({
                    "product_id": pid,
                    "title": title,
                    "status": status,
                })
                continue

            pub_date = datetime.strptime(pub_date_str, "%Y-%m-%d").date()
            expected_profile_name = pub_date_to_profile_name(pub_date)
            current = product_profile_map.get(pid)

            # Early stock arrivals with inventory on hand are exempt
            # from shipping profile requirements — they're fulfillable now
            if status == "early_stock_arrival" and inventory > 0:
                report["exempt"].append({
                    "product_id": pid,
                    "title": title,
                    "pub_date": pub_date_str,
                    "status": status,
                    "inventory": inventory,
                    "current_profile": current["profile_name"] if current else "General",
                    "reason": "Early stock on hand — fulfillable without date-based profile",
                })
                continue

            if pub_date <= today:
                # Past pub date — should NOT be on a date profile
                if current:
                    report["should_be_removed"].append({
                        "product_id": pid,
                        "title": title,
                        "pub_date": pub_date_str,
                        "current_profile": current["profile_name"],
                    })
                # else: correctly on General, no action needed
            else:
                # Future pub date — should be on matching date profile
                if not current:
                    report["missing_from_profile"].append({
                        "product_id": pid,
                        "title": title,
                        "pub_date": pub_date_str,
                        "expected_profile": expected_profile_name,
                    })
                elif current["profile_name"] != expected_profile_name:
                    report["wrong_profile"].append({
                        "product_id": pid,
                        "title": title,
                        "pub_date": pub_date_str,
                        "expected_profile": expected_profile_name,
                        "current_profile": current["profile_name"],
                    })
                else:
                    report["correctly_assigned"].append({
                        "product_id": pid,
                        "title": title,
                        "pub_date": pub_date_str,
                        "profile": expected_profile_name,
                    })

        return {
            "summary": {
                "correctly_assigned": len(report["correctly_assigned"]),
                "wrong_profile": len(report["wrong_profile"]),
                "missing_from_profile": len(report["missing_from_profile"]),
                "should_be_removed": len(report["should_be_removed"]),
                "exempt": len(report["exempt"]),
                "no_pub_date": len(report["no_pub_date"]),
            },
            "report": report,
        }

    except Exception as e:
        logger.error(f"Failed to reconcile shipping profiles: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


class RenameProfileRequest(BaseModel):
    new_name: str


@router.post("/shipping/profiles/{profile_id}/rename")
async def rename_shipping_profile(
    profile_id: int,
    request: RenameProfileRequest,
    ok: bool = Depends(require_admin_token),
):
    """
    Rename a delivery profile. Used to repurpose empty profiles for new pub dates,
    or correct non-standard profile names.
    """
    from services.shipping_profiles import rename_profile

    profile_gid = f"gid://shopify/DeliveryProfile/{profile_id}"
    client = ShopifyClient()
    try:
        errors = await rename_profile(client, profile_gid, request.new_name)
        if errors:
            raise HTTPException(status_code=500, detail={
                "message": "Failed to rename profile",
                "errors": errors,
            })
        return {
            "profile_id": profile_id,
            "action": "renamed",
            "new_name": request.new_name,
            "message": f"Profile renamed to '{request.new_name}'.",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to rename profile {profile_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()
