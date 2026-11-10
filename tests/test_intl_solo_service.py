"""
Tests for services/intl_solo_service.py (PR 1, read side).

The derivation is a pure function, so most tests call derive_state directly
with a fixed ET date. The read helpers run against tests/fakes.FakeSupabase.
"""

from datetime import date

import pytest

from services import intl_solo_service as svc
from tests.fakes import FakeSupabase

TODAY = date(2027, 1, 12)


def row(**overrides):
    base = {
        "product_id": 1,
        "title": "A Book",
        "isbn": "9780000000001",
        "classification": "active_preorder",
        "pub_date": "2027-02-01",
        "in_preorder_collection": True,
        "intl_solo_tag_present": True,
        "snapshot_at": "2027-01-11T12:00:00+00:00",
        "first_positive_inventory_at": None,
        "state": "restricted",
        "basis": "auto_preorder",
        "reason": None,
        "changed_by": None,
        "state_updated_at": "2027-01-01T12:00:00+00:00",
    }
    base.update(overrides)
    return base


def derive(**overrides):
    return svc.derive_state(row(**overrides), as_of=TODAY)


# ── Restricted, no stock ──────────────────────────────────────────

def test_restricted_future_pub_no_stock():
    d = derive()
    assert d["display_state"] == svc.RESTRICTED
    assert d["expected_tag"] is True
    assert d["drift"] is None
    assert d["allowed_actions"] == ["clear_guaranteed"]


def test_pub_date_reached_without_stock_is_hard_enforcement_case():
    # The owner's example: pub date 2027-01-12, no inventory yet.
    d = derive(pub_date="2027-01-12")
    assert d["display_state"] == svc.RESTRICTED_PAST_PUB
    assert d["expected_tag"] is True


def test_restriction_survives_full_cleanup():
    # After Full Cleanup the title is historical and out of the collection,
    # but with no stock and no clearance it stays restricted.
    d = derive(
        classification="historical_preorder",
        in_preorder_collection=False,
        pub_date="2026-12-01",
    )
    assert d["display_state"] == svc.RESTRICTED_PAST_PUB
    assert d["expected_tag"] is True


def test_restricted_without_pub_date_is_plain_restricted():
    d = derive(pub_date=None)
    assert d["display_state"] == svc.RESTRICTED


# ── Restricted, stock arrived ─────────────────────────────────────

def test_early_arrival_goes_to_review_not_auto_clear():
    d = derive(first_positive_inventory_at="2027-01-05T15:00:00+00:00")
    assert d["display_state"] == svc.EARLY_ARRIVAL_REVIEW
    assert d["expected_tag"] is True  # still restricted until a human clears it
    assert d["allowed_actions"] == ["clear_received", "clear_guaranteed"]


def test_arrival_after_pub_date_is_ready_to_clear():
    d = derive(
        pub_date="2027-01-08",
        first_positive_inventory_at="2027-01-10T15:00:00+00:00",
    )
    assert d["display_state"] == svc.READY_TO_CLEAR
    assert d["expected_tag"] is True
    assert d["allowed_actions"] == ["clear_received"]


def test_arrival_with_missing_pub_date_is_reviewed():
    d = derive(pub_date=None, first_positive_inventory_at="2027-01-05T15:00:00+00:00")
    assert d["display_state"] == svc.EARLY_ARRIVAL_REVIEW


def test_arrival_date_is_evaluated_in_eastern():
    # 03:00 UTC on Jan 13 is still Jan 12 in New York.
    d = derive(
        pub_date="2027-01-12",
        first_positive_inventory_at="2027-01-13T03:00:00+00:00",
    )
    assert d["arrival_date_et"] == "2027-01-12"


def test_negative_inventory_after_arrival_does_not_undo_arrival():
    # Arrival is the event; overselling afterwards does not matter. The view
    # carries no inventory column at all, so this is structural, but assert
    # the arrival still counts.
    d = derive(
        pub_date="2027-01-08",
        first_positive_inventory_at="2027-01-09T15:00:00+00:00",
        inventory=-4,
    )
    assert d["display_state"] == svc.READY_TO_CLEAR


# ── Cleared ───────────────────────────────────────────────────────

def test_cleared_expects_no_tag():
    d = derive(state="cleared", basis="received", reason="Stock in", intl_solo_tag_present=False)
    assert d["display_state"] == svc.CLEARED
    assert d["expected_tag"] is False
    assert d["drift"] is None
    assert d["allowed_actions"] == ["restore"]


def test_cleared_but_still_tagged_is_drift():
    d = derive(state="cleared", basis="guaranteed", reason="Confirmed", intl_solo_tag_present=True)
    assert d["drift"] == svc.DRIFT_TAG_UNEXPECTED


# ── Untracked ─────────────────────────────────────────────────────

def test_untracked_active_preorder_is_pending_not_drift():
    d = derive(state=None, basis=None, intl_solo_tag_present=False)
    assert d["display_state"] == svc.PENDING_RESTRICTION
    assert d["expected_tag"] is True
    assert d["drift"] is None


def test_untracked_early_stock_arrival_is_pending():
    d = derive(state=None, basis=None, classification="early_stock_arrival",
               in_preorder_collection=False, intl_solo_tag_present=False)
    assert d["display_state"] == svc.PENDING_RESTRICTION


def test_untracked_anomaly_in_collection_fails_toward_restricting():
    d = derive(state=None, basis=None, classification="anomaly_missing_tag",
               intl_solo_tag_present=False)
    assert d["display_state"] == svc.PENDING_RESTRICTION


def test_untracked_historical_title_is_not_applicable():
    # Titles released before this module existed are not swept in.
    d = derive(state=None, basis=None, classification="historical_preorder",
               in_preorder_collection=False, intl_solo_tag_present=False)
    assert d["display_state"] == svc.NOT_APPLICABLE
    assert d["expected_tag"] is False
    assert d["drift"] is None
    assert d["allowed_actions"] == ["force_on"]


def test_hand_tagged_non_preorder_is_drift():
    # e.g. the unlisted Cart Lock test books.
    d = derive(state=None, basis=None, classification="not_a_preorder_product",
               in_preorder_collection=False, intl_solo_tag_present=True)
    assert d["display_state"] == svc.NOT_APPLICABLE
    assert d["drift"] == svc.DRIFT_TAG_UNEXPECTED


def test_force_on_non_preorder():
    d = derive(state="restricted", basis="force_on", reason="Import only",
               classification="not_a_preorder_product", in_preorder_collection=False,
               pub_date=None)
    assert d["display_state"] == svc.RESTRICTED
    assert d["expected_tag"] is True


def test_restricted_but_untagged_is_drift():
    d = derive(intl_solo_tag_present=False)
    assert d["drift"] == svc.DRIFT_TAG_MISSING


def test_unknown_state_is_treated_as_untracked():
    d = derive(state="bogus", intl_solo_tag_present=False)
    assert d["display_state"] == svc.PENDING_RESTRICTION


def test_every_display_state_has_actions():
    assert set(svc.ALLOWED_ACTIONS) == set(svc.DISPLAY_STATES)


# ── Reads (FakeSupabase) ──────────────────────────────────────────

def seeded():
    db = FakeSupabase()
    db.seed("preorder", "vw_intl_solo_inputs", [
        row(product_id=1, pub_date="2027-02-01"),
        row(product_id=2, pub_date="2027-01-12"),
        row(product_id=3, state=None, basis=None, classification="historical_preorder",
            in_preorder_collection=False, intl_solo_tag_present=False, pub_date="2025-05-01"),
        row(product_id=4, state=None, basis=None, classification="not_a_preorder_product",
            in_preorder_collection=False, intl_solo_tag_present=True, pub_date=None),
    ])
    return db


def test_list_states_hides_quiet_not_applicable_but_keeps_drift():
    states = svc.list_states(seeded(), as_of=TODAY)
    ids = [s["product_id"] for s in states]
    assert 3 not in ids          # quiet historical title hidden
    assert 4 in ids              # stray tag shown
    assert ids.index(2) < ids.index(1)  # ordered by pub date


def test_list_states_can_include_everything():
    states = svc.list_states(seeded(), include_not_applicable=True, as_of=TODAY)
    assert {s["product_id"] for s in states} == {1, 2, 3, 4}


def test_summarize_counts():
    summary = svc.summarize(svc.list_states(seeded(), as_of=TODAY))
    assert summary[svc.RESTRICTED] == 1
    assert summary[svc.RESTRICTED_PAST_PUB] == 1
    assert summary[svc.NOT_APPLICABLE] == 1
    assert summary["drift"] == 1


def test_list_log_filters_and_orders():
    db = FakeSupabase()
    db.seed("preorder", "intl_solo_log", [
        {"id": 1, "product_id": 1, "action": "restrict", "performed_at": "2027-01-01T00:00:00+00:00"},
        {"id": 2, "product_id": 2, "action": "restrict", "performed_at": "2027-01-02T00:00:00+00:00"},
        {"id": 3, "product_id": 1, "action": "clear", "performed_at": "2027-01-03T00:00:00+00:00"},
    ])
    entries = svc.list_log(db, product_id=1)
    assert [e["id"] for e in entries] == [3, 1]


# ── Confirmed tag writes vs the classification snapshot ───────────

def test_confirmed_write_newer_than_snapshot_wins():
    # Just restored: decision and confirmed write say tagged, but the snapshot
    # predates the write. No false "tag missing".
    d = derive(intl_solo_tag_present=False,
               snapshot_at="2027-01-11T12:00:00+00:00",
               tag_confirmed=True, tag_confirmed_at="2027-01-11T12:00:05+00:00")
    assert d["tag_present"] is True
    assert d["tag_source"] == "confirmed_write"
    assert d["drift"] is None


def test_confirmed_clear_newer_than_snapshot_wins():
    d = derive(state="cleared", basis="received", reason="Stock in",
               intl_solo_tag_present=True,
               snapshot_at="2027-01-11T12:00:00+00:00",
               tag_confirmed=False, tag_confirmed_at="2027-01-11T12:00:05+00:00")
    assert d["drift"] is None


def test_newer_snapshot_overrides_confirmed_write():
    # Someone removed the tag in Shopify admin after our write: real drift.
    d = derive(intl_solo_tag_present=False,
               snapshot_at="2027-01-11T12:05:00+00:00",
               tag_confirmed=True, tag_confirmed_at="2027-01-11T12:00:05+00:00")
    assert d["tag_source"] == "snapshot"
    assert d["drift"] == svc.DRIFT_TAG_MISSING


def test_confirmed_write_without_snapshot_time_is_trusted():
    d = derive(intl_solo_tag_present=False, snapshot_at=None,
               tag_confirmed=True, tag_confirmed_at="2027-01-11T12:00:05+00:00")
    assert d["tag_present"] is True


def test_no_confirmed_write_uses_snapshot():
    d = derive(intl_solo_tag_present=False)
    assert d["tag_source"] == "snapshot"
    assert d["drift"] == svc.DRIFT_TAG_MISSING
