"""
services/intl_solo_service.py

International cart isolation ("intl-solo"). PR 1: read side only.

The Shopify tag `intl-solo` is what the Cart Lock checkout rule enforces: an
order shipping outside the US that contains an intl-solo product may contain
only that one product (any quantity). preorder.intl_solo_state is the decision
of record for what the tag SHOULD be. This module derives, per product, a
display state, the expected tag, any drift, and the actions the dashboard may
offer. The dashboard renders these; it does not re-derive them.

Lifecycle rules: db/migrations/2026_11_create_intl_solo_state.sql and
docs/intl_solo.md. In short, intl-solo is independent of the domestic
preorder lifecycle and is cleared only by a manual decision.

TIME ZONE: dates are evaluated in America/New_York via hold_service.today_et(),
the same clock holds use, so the two never disagree for part of a day.

No Shopify writes and no Supabase writes happen in this module (PR 1).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from services.hold_service import today_et

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
SCHEMA = "preorder"
INPUTS_VIEW = "vw_intl_solo_inputs"
LOG_TABLE = "intl_solo_log"

TAG = "intl-solo"

# Classifications that make an untracked product a restriction candidate.
# Collection membership is checked separately, so anomalies that are still in
# the Preorder collection are candidates too (fail toward restricting).
CANDIDATE_CLASSIFICATIONS = frozenset({"active_preorder", "early_stock_arrival"})

# Display states, in rough order of operator attention.
RESTRICTED_PAST_PUB = "restricted_past_pub"   # hard-enforcement case
EARLY_ARRIVAL_REVIEW = "early_arrival_review"  # stock before pub date: case by case
READY_TO_CLEAR = "ready_to_clear"              # stock arrived on/after pub date
RESTRICTED = "restricted"
PENDING_RESTRICTION = "pending_restriction"    # candidate, no decision yet (backfill/auto-tag will restrict)
CLEARED = "cleared"
NOT_APPLICABLE = "not_applicable"

DISPLAY_STATES = (
    RESTRICTED_PAST_PUB,
    EARLY_ARRIVAL_REVIEW,
    READY_TO_CLEAR,
    RESTRICTED,
    PENDING_RESTRICTION,
    CLEARED,
    NOT_APPLICABLE,
)

# Drift kinds: the Shopify tag (as of the last snapshot) disagrees with the
# decision of record.
DRIFT_TAG_MISSING = "tag_missing"        # should be tagged, is not
DRIFT_TAG_UNEXPECTED = "tag_unexpected"  # tagged, should not be (e.g. hand-tagged test titles)

# Actions the dashboard may offer for each display state. Endpoints for these
# arrive in PR 2; listing them here keeps the UI from computing eligibility.
ALLOWED_ACTIONS: Dict[str, List[str]] = {
    RESTRICTED: ["clear_guaranteed"],
    RESTRICTED_PAST_PUB: ["clear_guaranteed"],
    EARLY_ARRIVAL_REVIEW: ["clear_received", "clear_guaranteed"],
    READY_TO_CLEAR: ["clear_received"],
    CLEARED: ["restore"],
    PENDING_RESTRICTION: [],
    NOT_APPLICABLE: ["force_on"],
}


# ──────────────────────────────────────────────
# Parsing helpers
# ──────────────────────────────────────────────

def _as_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_et_date(value: Any) -> Optional[date]:
    """A timestamptz (string or datetime) as a calendar date in Eastern."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return _as_date(value)
    if dt.tzinfo is None:
        # Naive timestamps are treated as UTC, matching Supabase's storage.
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(ET).date()


def _as_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt


def effective_tag(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    What the Shopify tag is, as best we know.

    Two sources: the classification snapshot (product_status tags, refreshed
    when Shopify's products/update webhook is processed) and the last tag
    write this module made that Shopify confirmed (tag_confirmed /
    tag_confirmed_at). Whichever is newer wins.

    Without this, every action showed a few seconds of false drift: the
    decision changed at once, but the snapshot only caught up when the
    webhook arrived. A snapshot taken after the write still overrides it, so
    a later change made in Shopify admin shows as drift as before.
    """
    snapshot = bool(row.get("intl_solo_tag_present"))
    confirmed = row.get("tag_confirmed")
    confirmed_at = _as_datetime(row.get("tag_confirmed_at"))
    snapshot_at = _as_datetime(row.get("snapshot_at"))
    if confirmed is not None and confirmed_at is not None and (
        snapshot_at is None or confirmed_at > snapshot_at
    ):
        return {"present": bool(confirmed), "source": "confirmed_write"}
    return {"present": snapshot, "source": "snapshot"}


def is_candidate(row: Dict[str, Any]) -> bool:
    """True when an untracked product should become restricted."""
    return (
        row.get("classification") in CANDIDATE_CLASSIFICATIONS
        or bool(row.get("in_preorder_collection"))
    )


# ──────────────────────────────────────────────
# Pure derivation
# ──────────────────────────────────────────────

def derive_state(row: Dict[str, Any], as_of: Optional[date] = None) -> Dict[str, Any]:
    """
    Derive intl-solo state for one row of vw_intl_solo_inputs.

    Pure: no I/O. `as_of` is the ET date to evaluate against (defaults to
    today in ET).

    Returns the row's identifying fields plus:
      display_state     one of DISPLAY_STATES
      expected_tag      bool, what the Shopify tag should be
      tag_present       bool, the tag as best known (see effective_tag)
      tag_source        "snapshot" | "confirmed_write"
      drift             None | DRIFT_TAG_MISSING | DRIFT_TAG_UNEXPECTED
      arrival_date_et   date of first positive inventory in ET, or None
      allowed_actions   list of action names for the dashboard
    """
    as_of = as_of or today_et()
    state = row.get("state")
    tag = effective_tag(row)
    tag_present = tag["present"]
    pub_date = _as_date(row.get("pub_date"))
    arrival_date = _as_et_date(row.get("first_positive_inventory_at"))

    if state == "cleared":
        display, expected = CLEARED, False
    elif state == "restricted":
        expected = True
        if arrival_date is None:
            past_pub = pub_date is not None and pub_date <= as_of
            display = RESTRICTED_PAST_PUB if past_pub else RESTRICTED
        elif pub_date is None or pub_date > as_of:
            # Stock is in but the title has not published: case by case.
            display = EARLY_ARRIVAL_REVIEW
        else:
            display = READY_TO_CLEAR
    else:
        if state is not None:
            logger.warning(
                f"[intl-solo] unknown state {state!r} for product {row.get('product_id')}; "
                "treating as untracked"
            )
        if is_candidate(row):
            # No decision yet. Not drift: the auto-tag/backfill step (PR 3)
            # records the decision and applies the tag.
            display, expected = PENDING_RESTRICTION, True
        else:
            display, expected = NOT_APPLICABLE, False

    if display == PENDING_RESTRICTION:
        drift = None
    elif expected and not tag_present:
        drift = DRIFT_TAG_MISSING
    elif tag_present and not expected:
        drift = DRIFT_TAG_UNEXPECTED
    else:
        drift = None

    return {
        "product_id": row.get("product_id"),
        "title": row.get("title"),
        "isbn": row.get("isbn"),
        "classification": row.get("classification"),
        "pub_date": pub_date.isoformat() if pub_date else None,
        "in_preorder_collection": bool(row.get("in_preorder_collection")),
        "first_positive_inventory_at": row.get("first_positive_inventory_at"),
        "arrival_date_et": arrival_date.isoformat() if arrival_date else None,
        "state": state,
        "basis": row.get("basis"),
        "reason": row.get("reason"),
        "changed_by": row.get("changed_by"),
        "state_updated_at": row.get("state_updated_at"),
        "snapshot_at": row.get("snapshot_at"),
        "display_state": display,
        "expected_tag": expected,
        "tag_present": tag_present,
        "tag_source": tag["source"],
        "drift": drift,
        "allowed_actions": list(ALLOWED_ACTIONS[display]),
    }


def summarize(states: List[Dict[str, Any]]) -> Dict[str, int]:
    """Counts per display state, plus a drift total."""
    counts = {s: 0 for s in DISPLAY_STATES}
    drift = 0
    for s in states:
        counts[s["display_state"]] = counts.get(s["display_state"], 0) + 1
        if s["drift"]:
            drift += 1
    counts["drift"] = drift
    return counts


# ──────────────────────────────────────────────
# Reads
# ──────────────────────────────────────────────

def list_states(
    supabase,
    include_not_applicable: bool = False,
    as_of: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """
    Derived state for every product in vw_intl_solo_inputs, ordered by pub date.

    not_applicable rows are hidden by default, except when they drift (a stray
    tag on a non-preorder is exactly what an operator needs to see).
    """
    as_of = as_of or today_et()
    resp = (
        supabase.schema(SCHEMA)
        .table(INPUTS_VIEW)
        .select("*")
        .order("pub_date")
        .execute()
    )
    out = []
    for row in resp.data or []:
        derived = derive_state(row, as_of=as_of)
        if (
            derived["display_state"] == NOT_APPLICABLE
            and not derived["drift"]
            and not include_not_applicable
        ):
            continue
        out.append(derived)
    return out


def list_log(
    supabase,
    product_id: Optional[int] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Audit log entries, newest first, optionally for one product."""
    query = supabase.schema(SCHEMA).table(LOG_TABLE).select("*")
    if product_id is not None:
        query = query.eq("product_id", product_id)
    resp = query.order("performed_at", desc=True).limit(limit).execute()
    return resp.data or []
