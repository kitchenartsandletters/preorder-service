"""
services/week_migration.py — Phase 2 preview-and-apply engine.

Computes the week-based grouping plan for active preorders (read-only), and
applies it one release week at a time on the verified builder + resolver.

The heart is the pure function `_compute_week_plan`, which takes plain data
(preorders, current profile assignments, the week mapping, existing profiles)
and returns the diff — no I/O, fully testable. `build_week_plan` fetches the
inputs; `apply_week_plan` executes a single week with a preflight check.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from services.release_week import week_start_for, week_profile_name
from services.shipping_profiles import (
    list_shipping_profiles,
    assign_product_to_profile,
    preview_reference_clone,
)
from services.week_profiles import find_or_create_profile_for_week

logger = logging.getLogger(__name__)

SCHEMA = "preorder"
WEEK_TABLE = "shipping_profile_week"


# ──────────────────────────────────────────────
# Pure planner
# ──────────────────────────────────────────────

def _compute_week_plan(
    preorders: List[Dict[str, Any]],
    product_profile_map: Dict[int, Dict[str, str]],
    non_default_profiles: List[Dict[str, Any]],
    week_mapping: Dict[date, str],
    profiles_by_name: Dict[str, Dict[str, Any]],
    today: date,
) -> Dict[str, Any]:
    """
    Compute the week-based grouping plan.

    preorders: [{product_id, status, pub_date: date|None, title, inventory}]
    product_profile_map: {product_id: {"profile_name", "profile_gid"}} (current,
        non-default assignments only)
    non_default_profiles: [{"profile_gid","name","products":[{product_id,...}]}]
    week_mapping: {week_start(date): profile_gid}
    profiles_by_name: {name: profile_dict}
    today: date boundary for past vs future

    Returns a plan dict: weeks[], emptied_profiles[], should_be_removed[],
    exempt[], no_pub_date[], summary{}.
    """
    weeks: Dict[date, Dict[str, Any]] = {}
    exempt: List[Dict[str, Any]] = []
    should_be_removed: List[Dict[str, Any]] = []
    no_pub_date: List[Dict[str, Any]] = []

    for po in preorders:
        pid = po["product_id"]
        title = po.get("title") or f"Product {pid}"
        status = po.get("status")
        pub = po.get("pub_date")
        inv = int(po.get("inventory") or 0)
        current = product_profile_map.get(pid)

        if pub is None:
            no_pub_date.append({"product_id": pid, "title": title, "status": status})
            continue
        if status == "early_stock_arrival" and inv > 0:
            exempt.append({
                "product_id": pid, "title": title, "pub_date": pub.isoformat(),
                "inventory": inv,
                "current_profile": current["profile_name"] if current else "General",
            })
            continue
        if pub <= today:
            if current:
                should_be_removed.append({
                    "product_id": pid, "title": title, "pub_date": pub.isoformat(),
                    "current_profile": current["profile_name"],
                })
            continue

        ws = week_start_for(pub)
        we = ws + timedelta(days=6)
        name = week_profile_name(ws)
        target_gid = week_mapping.get(ws)
        if not target_gid:
            existing = profiles_by_name.get(name)
            target_gid = existing["profile_gid"] if existing else None

        wk = weeks.setdefault(ws, {
            "week_start": ws.isoformat(),
            "week_end": we.isoformat(),
            "profile_name": name,
            "profile_status": "exists" if target_gid else "create",
            "profile_gid": target_gid,
            "titles": [],
        })

        if target_gid and current and current["profile_gid"] == target_gid:
            action = "already"
        elif current is None:
            action = "add"
        else:
            action = "move"

        wk["titles"].append({
            "product_id": pid, "title": title, "pub_date": pub.isoformat(),
            "current_profile": current["profile_name"] if current else "General",
            "action": action,
        })

    # Emptied profiles: non-default profiles whose every current product moves
    # to a different profile.
    moving_targets: Dict[int, Optional[str]] = {}
    for wk in weeks.values():
        for t in wk["titles"]:
            if t["action"] in ("move", "add"):
                moving_targets[t["product_id"]] = wk["profile_gid"]

    emptied: List[Dict[str, Any]] = []
    for p in non_default_profiles:
        prods = p.get("products") or []
        if not prods:
            continue
        all_leaving = True
        for pr in prods:
            tgid = moving_targets.get(pr.get("product_id"), "STAY")
            if tgid == p["profile_gid"] or tgid == "STAY":
                all_leaving = False
                break
        if all_leaving:
            emptied.append({
                "profile_gid": p["profile_gid"], "name": p.get("name"),
                "products_now": len(prods),
            })

    weeks_list = [weeks[k] for k in sorted(weeks)]
    summary = {
        "weeks_total": len(weeks_list),
        "weeks_to_create": sum(1 for w in weeks_list if w["profile_status"] == "create"),
        "titles_total": sum(len(w["titles"]) for w in weeks_list),
        "titles_moving": sum(1 for w in weeks_list for t in w["titles"] if t["action"] in ("move", "add")),
        "titles_already": sum(1 for w in weeks_list for t in w["titles"] if t["action"] == "already"),
        "profiles_emptied": len(emptied),
        "should_be_removed": len(should_be_removed),
        "exempt": len(exempt),
        "no_pub_date": len(no_pub_date),
    }
    return {
        "weeks": weeks_list,
        "emptied_profiles": emptied,
        "should_be_removed": should_be_removed,
        "exempt": exempt,
        "no_pub_date": no_pub_date,
        "summary": summary,
    }


# ──────────────────────────────────────────────
# Fetch + orchestrate
# ──────────────────────────────────────────────

def _fetch_active_preorders(supabase: Any) -> List[Dict[str, Any]]:
    resp = (
        supabase.schema(SCHEMA)
        .table("product_status")
        .select("product_id, status, effective_pub_date, metadata_snapshot")
        .in_("status", ["active_preorder", "early_stock_arrival"])
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    out = []
    for r in rows:
        pub_str = r.get("effective_pub_date")
        meta = r.get("metadata_snapshot") or {}
        out.append({
            "product_id": r["product_id"],
            "status": r.get("status"),
            "pub_date": date.fromisoformat(pub_str) if pub_str else None,
            "title": meta.get("title"),
            "inventory": meta.get("inventory", 0),
        })
    return out


def _fetch_week_mapping(supabase: Any) -> Dict[date, str]:
    resp = supabase.schema(SCHEMA).table(WEEK_TABLE).select("profile_gid, week_start").execute()
    rows = getattr(resp, "data", None) or []
    return {date.fromisoformat(r["week_start"]): r["profile_gid"] for r in rows}


async def build_week_plan(shopify_client: Any, supabase: Any) -> Dict[str, Any]:
    """Read-only: fetch inputs and compute the week plan. Creates nothing."""
    profiles = await list_shipping_profiles(shopify_client)
    non_default = [p for p in profiles if not p.get("is_default")]
    profiles_by_name = {p["name"]: p for p in non_default}

    product_profile_map: Dict[int, Dict[str, str]] = {}
    for p in non_default:
        for prod in p.get("products") or []:
            if prod.get("product_id"):
                product_profile_map[prod["product_id"]] = {
                    "profile_name": p["name"],
                    "profile_gid": p["profile_gid"],
                }

    preorders = _fetch_active_preorders(supabase)
    week_mapping = _fetch_week_mapping(supabase)

    return _compute_week_plan(
        preorders=preorders,
        product_profile_map=product_profile_map,
        non_default_profiles=non_default,
        week_mapping=week_mapping,
        profiles_by_name=profiles_by_name,
        today=date.today(),
    )


async def apply_week_plan(shopify_client: Any, supabase: Any, week_start: date) -> Dict[str, Any]:
    """
    Apply the plan for a SINGLE release week: ensure the week profile exists
    (creating on the verified builder if needed) and assign every title in that
    week to it. Preflight-checks the reference clone before any create.
    """
    plan = await build_week_plan(shopify_client, supabase)
    wk = next((w for w in plan["weeks"] if w["week_start"] == week_start.isoformat()), None)
    if not wk:
        return {
            "week_start": week_start.isoformat(),
            "status": "noop",
            "message": "No active future preorders for this week.",
        }

    titles = wk["titles"]

    # Preflight: if a profile must be created, confirm the reference clone is
    # healthy before we mutate anything. Raises (surfaced as 400) if not.
    if wk["profile_status"] == "create":
        await preview_reference_clone(shopify_client)

    seed = titles[0]
    profile = await find_or_create_profile_for_week(
        shopify_client, supabase,
        date.fromisoformat(seed["pub_date"]), seed["product_id"],
    )
    profile_gid = profile["profile_gid"]

    assigned: List[int] = []
    errors: List[Dict[str, Any]] = []
    for t in titles:
        errs = await assign_product_to_profile(shopify_client, profile_gid, t["product_id"])
        if errs:
            errors.append({"product_id": t["product_id"], "errors": errs})
        else:
            assigned.append(t["product_id"])

    return {
        "week_start": week_start.isoformat(),
        "week_end": wk["week_end"],
        "profile_name": wk["profile_name"],
        "profile_gid": profile_gid,
        "created": wk["profile_status"] == "create",
        "assigned": assigned,
        "errors": errors,
        "status": "applied" if not errors else "applied_with_errors",
    }
