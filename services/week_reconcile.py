"""
services/week_reconcile.py — week-aware reconcile.

Re-expresses the shipping reconcile against the week model: a title is
`correctly_assigned` when it sits on the delivery profile mapped to its Sun–Sat
release week (mapping table first, name second). Same six buckets as the
date-based reconcile, so the dashboard reads it unchanged; adds a `migration`
progress block (titles on their week profile vs still needing to move, and
profiles now empty and ready to repurpose).

Titles still on their old per-date profile show as `wrong_profile` — under the
week model that's the correct signal: they are the migration to-do list.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List

from services.release_week import week_start_for, week_profile_name
from services.shipping_profiles import list_shipping_profiles
from services.week_migration import _fetch_active_preorders, _fetch_week_mapping

logger = logging.getLogger(__name__)


def compute_week_reconcile(
    preorders: List[Dict[str, Any]],
    product_profile_map: Dict[int, Dict[str, str]],
    non_default_profiles: List[Dict[str, Any]],
    week_mapping: Dict[date, str],
    profiles_by_name: Dict[str, Dict[str, Any]],
    today: date,
) -> Dict[str, Any]:
    """
    Pure week-aware reconcile. Inputs mirror the planner's. Returns the six-bucket
    report (dashboard-compatible) plus `model` and a `migration` progress block.
    """
    report: Dict[str, List[Dict[str, Any]]] = {
        "correctly_assigned": [],
        "wrong_profile": [],
        "missing_from_profile": [],
        "should_be_removed": [],
        "exempt": [],
        "no_pub_date": [],
    }

    for po in preorders:
        pid = po["product_id"]
        title = po.get("title") or f"Product {pid}"
        status = po.get("status")
        pub = po.get("pub_date")
        inv = int(po.get("inventory") or 0)
        current = product_profile_map.get(pid)

        if pub is None:
            report["no_pub_date"].append({"product_id": pid, "title": title, "status": status})
            continue
        if status == "early_stock_arrival" and inv > 0:
            report["exempt"].append({
                "product_id": pid, "title": title, "pub_date": pub.isoformat(),
                "status": status, "inventory": inv,
                "current_profile": current["profile_name"] if current else "General",
                "reason": "Early stock on hand — fulfillable without a date/week profile",
            })
            continue
        if pub <= today:
            if current:
                report["should_be_removed"].append({
                    "product_id": pid, "title": title, "pub_date": pub.isoformat(),
                    "current_profile": current["profile_name"],
                })
            continue

        ws = week_start_for(pub)
        name = week_profile_name(ws)
        target_gid = week_mapping.get(ws)
        if not target_gid:
            existing = profiles_by_name.get(name)
            target_gid = existing["profile_gid"] if existing else None

        if not current:
            report["missing_from_profile"].append({
                "product_id": pid, "title": title, "pub_date": pub.isoformat(),
                "expected_profile": name,
            })
        elif target_gid and current["profile_gid"] == target_gid:
            report["correctly_assigned"].append({
                "product_id": pid, "title": title, "pub_date": pub.isoformat(),
                "profile": name,
            })
        else:
            report["wrong_profile"].append({
                "product_id": pid, "title": title, "pub_date": pub.isoformat(),
                "expected_profile": name, "current_profile": current["profile_name"],
            })

    repurpose_ready = [
        {"profile_gid": p["profile_gid"], "name": p.get("name")}
        for p in non_default_profiles if not (p.get("products") or [])
    ]
    migration = {
        "titles_on_week_profile": len(report["correctly_assigned"]),
        "titles_needing_migration": len(report["wrong_profile"]) + len(report["missing_from_profile"]),
        "repurpose_ready_profiles": repurpose_ready,
    }
    summary = {k: len(v) for k, v in report.items()}
    return {"model": "week", "summary": summary, "report": report, "migration": migration}


async def build_week_reconcile(shopify_client: Any, supabase: Any) -> Dict[str, Any]:
    """Fetch inputs and run the week-aware reconcile. Read-only."""
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

    return compute_week_reconcile(
        preorders=preorders,
        product_profile_map=product_profile_map,
        non_default_profiles=non_default,
        week_mapping=week_mapping,
        profiles_by_name=profiles_by_name,
        today=date.today(),
    )
