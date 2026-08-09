"""
services/week_profiles.py — week-based shipping profile resolution.

Sits on top of services/shipping_profiles.py (the Shopify primitive layer) and
services/release_week.py (the canonical Sun–Sat week). Resolves the single
delivery profile that groups all preorders whose pub dates fall in the same
release week, minting one on the verified builder when needed.

The canonical week↔profile key lives in preorder.shipping_profile_week
(profile_gid PK, week_start unique). Profile display names are derived from
week_start via week_profile_name and are never parsed back into dates.

Resolution order for a pub date:
  1. Mapping table — if week_start maps to a profile that still exists, use it.
     (A mapping row whose Shopify profile was deleted is pruned and we fall
     through.)
  2. Adopt by name — a profile already named for the week but not yet mapped
     (e.g. created manually) is recorded and returned.
  3. Create — mint a new profile from the reference template, record the
     mapping, return it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from services.release_week import release_week, week_start_for, week_profile_name
from services.shipping_profiles import (
    list_shipping_profiles,
    get_profile_detail,
    get_variant_gid_for_product,
    create_profile_from_template,
)

logger = logging.getLogger(__name__)

SCHEMA = "preorder"
WEEK_TABLE = "shipping_profile_week"


# ──────────────────────────────────────────────
# Mapping table I/O  (preorder.shipping_profile_week)
# ──────────────────────────────────────────────

def get_profile_gid_for_week(supabase: Any, week_start: date) -> Optional[str]:
    """Return the profile_gid mapped to this week_start, or None."""
    resp = (
        supabase.schema(SCHEMA)
        .table(WEEK_TABLE)
        .select("profile_gid")
        .eq("week_start", week_start.isoformat())
        .limit(1)
        .execute()
    )
    rows = getattr(resp, "data", None) or []
    return rows[0]["profile_gid"] if rows else None


def record_week_profile(supabase: Any, profile_gid: str, week_start: date) -> None:
    """Upsert the profile_gid↔week_start mapping (keyed on profile_gid)."""
    supabase.schema(SCHEMA).table(WEEK_TABLE).upsert(
        {
            "profile_gid": profile_gid,
            "week_start": week_start.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="profile_gid",
    ).execute()


def delete_week_mapping(supabase: Any, profile_gid: str) -> None:
    """Remove a mapping row (used to prune a stale mapping to a deleted profile)."""
    supabase.schema(SCHEMA).table(WEEK_TABLE).delete().eq("profile_gid", profile_gid).execute()


# ──────────────────────────────────────────────
# Resolver
# ──────────────────────────────────────────────

async def _get_profile_if_exists(shopify_client: Any, profile_gid: str) -> Optional[Dict[str, Any]]:
    try:
        return await get_profile_detail(shopify_client, profile_gid)
    except ValueError:
        return None


async def find_or_create_profile_for_week(
    shopify_client: Any,
    supabase: Any,
    pub_date: date,
    product_id: int,
    variant_gid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve the delivery profile for the release week containing `pub_date`,
    creating it if necessary. Returns the profile dict (with week_start/week_end
    added). Does not associate the product beyond the builder's create-time
    association — the caller assigns the variant, mirroring the date flow.
    """
    week_start, week_end = release_week(pub_date)
    target_name = week_profile_name(week_start)
    logger.info(
        f"[week_resolver] pub_date={pub_date.isoformat()} week={week_start.isoformat()}"
        f"..{week_end.isoformat()} name={target_name!r}"
    )

    # 1. Canonical mapping
    mapped_gid = get_profile_gid_for_week(supabase, week_start)
    if mapped_gid:
        profile = await _get_profile_if_exists(shopify_client, mapped_gid)
        if profile:
            profile["week_start"] = week_start.isoformat()
            profile["week_end"] = week_end.isoformat()
            return profile
        logger.warning(
            f"[week_resolver] mapping for {week_start.isoformat()} -> {mapped_gid} "
            f"points at a missing profile; pruning and re-resolving."
        )
        delete_week_mapping(supabase, mapped_gid)

    # 2. Adopt an existing, unmapped profile named for this week
    profiles = await list_shipping_profiles(shopify_client)
    for p in profiles:
        if not p.get("is_default") and p.get("name") == target_name:
            logger.info(f"[week_resolver] adopting existing profile {p['profile_gid']} for {target_name!r}")
            record_week_profile(supabase, p["profile_gid"], week_start)
            p["week_start"] = week_start.isoformat()
            p["week_end"] = week_end.isoformat()
            return p

    # 3. Create from the verified reference template
    if not variant_gid:
        variant_gid = await get_variant_gid_for_product(shopify_client, product_id)
    logger.info(f"[week_resolver] creating new week profile {target_name!r} variant={variant_gid}")
    created = await create_profile_from_template(shopify_client, target_name, variant_gid)
    record_week_profile(supabase, created["profile_gid"], week_start)
    created["week_start"] = week_start.isoformat()
    created["week_end"] = week_end.isoformat()
    return created
