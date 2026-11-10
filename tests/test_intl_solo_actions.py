"""
Tests for services/intl_solo_actions.py (PR 2, write side).

Supabase is tests/fakes.FakeSupabase. Shopify is a small recording fake that
can be told to return userErrors, so the fail-toward-restricting write order
is asserted on resulting state, not on mock calls.
"""

from datetime import date

import pytest

from services import intl_solo_actions as actions
from services import intl_solo_service as svc
from tests.fakes import FakeSupabase

TODAY = date(2027, 1, 12)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(actions, "today_et", lambda: TODAY)
    monkeypatch.setattr(svc, "today_et", lambda: TODAY)


class FakeShopify:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def graphql(self, query, variables=None):
        op = "tagsAdd" if "tagsAdd(" in query else "tagsRemove"
        self.calls.append((op, variables["id"], tuple(variables["tags"])))
        errors = [{"field": ["id"], "message": "nope"}] if self.fail else []
        return {op: {"node": {"id": variables["id"]}, "userErrors": errors}}


def view_row(**overrides):
    base = {
        "product_id": 1, "title": "A Book", "isbn": None,
        "classification": "active_preorder", "pub_date": "2027-02-01",
        "in_preorder_collection": True, "intl_solo_tag_present": True,
        "snapshot_at": None, "first_positive_inventory_at": None,
        "state": "restricted", "basis": "auto_preorder", "reason": None,
        "changed_by": None, "state_updated_at": None,
    }
    base.update(overrides)
    return base


def db_with(*rows):
    db = FakeSupabase()
    db.seed("preorder", "vw_intl_solo_inputs", list(rows))
    return db


def state_of(db, pid=1):
    rows = [r for r in db.rows("preorder", "intl_solo_state") if r["product_id"] == pid]
    return rows[0] if rows else None


def log_of(db):
    return db.rows("preorder", "intl_solo_log")


# ── Tag mutations ─────────────────────────────────────────────────

async def test_tag_writes_use_tagsadd_and_tagsremove_only():
    shop = FakeShopify()
    await actions.add_tag(shop, 7)
    await actions.remove_tag(shop, 7)
    assert shop.calls == [
        ("tagsAdd", "gid://shopify/Product/7", ("intl-solo",)),
        ("tagsRemove", "gid://shopify/Product/7", ("intl-solo",)),
    ]
    assert "productUpdate" not in actions.TAGS_ADD_MUTATION + actions.TAGS_REMOVE_MUTATION


# ── clear ─────────────────────────────────────────────────────────

async def test_clear_guaranteed_records_then_removes_tag():
    db, shop = db_with(view_row()), FakeShopify()
    out = await actions.clear(db, shop, 1, "guaranteed", "Distributor confirmed", "gil")
    assert out["result"] == "cleared"
    s = state_of(db)
    assert (s["state"], s["basis"], s["reason"], s["changed_by"]) == (
        "cleared", "guaranteed", "Distributor confirmed", "gil")
    assert shop.calls[0][0] == "tagsRemove"
    assert log_of(db)[-1]["action"] == "clear" and log_of(db)[-1]["success"] is True


async def test_clear_received_requires_an_arrival():
    db = db_with(view_row())  # restricted, no stock
    with pytest.raises(actions.IntlSoloActionError):
        await actions.clear(db, FakeShopify(), 1, "received", "Stock in")


async def test_clear_received_after_arrival():
    db = db_with(view_row(pub_date="2027-01-08",
                          first_positive_inventory_at="2027-01-10T15:00:00+00:00"))
    out = await actions.clear(db, FakeShopify(), 1, "received", "Stock in")
    assert out["from_state"] == svc.READY_TO_CLEAR
    assert state_of(db)["basis"] == "received"


async def test_clear_requires_reason():
    with pytest.raises(ValueError):
        await actions.clear(db_with(view_row()), FakeShopify(), 1, "guaranteed", "   ")


async def test_clear_rejects_unknown_basis():
    with pytest.raises(ValueError):
        await actions.clear(db_with(view_row()), FakeShopify(), 1, "vibes", "why")


async def test_clear_shopify_failure_stays_restricted_at_checkout():
    # Decision is recorded, the tag stays on: enforced, shows as drift.
    db, shop = db_with(view_row()), FakeShopify(fail=True)
    with pytest.raises(actions.IntlSoloShopifyError):
        await actions.clear(db, shop, 1, "guaranteed", "Confirmed")
    assert state_of(db)["state"] == "cleared"
    assert log_of(db)[-1]["success"] is False


async def test_clear_unknown_product_is_lookup_error():
    with pytest.raises(LookupError):
        await actions.clear(FakeSupabase(), FakeShopify(), 99, "guaranteed", "x")


# ── restore ───────────────────────────────────────────────────────

async def test_restore_returns_preorder_to_auto_basis():
    db = db_with(view_row(state="cleared", basis="guaranteed", reason="r",
                          intl_solo_tag_present=False))
    shop = FakeShopify()
    out = await actions.restore(db, shop, 1, "Cleared by mistake")
    assert out["result"] == "restricted"
    assert shop.calls[0][0] == "tagsAdd"
    s = state_of(db)
    assert (s["state"], s["basis"]) == ("restricted", "auto_preorder")


async def test_restore_non_preorder_uses_force_on_basis():
    db = db_with(view_row(state="cleared", basis="guaranteed", reason="r",
                          classification="historical_preorder", in_preorder_collection=False,
                          intl_solo_tag_present=False))
    await actions.restore(db, FakeShopify(), 1, "Mistake")
    assert state_of(db)["basis"] == "force_on"


async def test_restore_only_from_cleared():
    with pytest.raises(actions.IntlSoloActionError):
        await actions.restore(db_with(view_row()), FakeShopify(), 1, "why")


async def test_restrict_shopify_failure_records_nothing():
    # Tag first: if Shopify refuses, no decision is written.
    db = db_with(view_row(state="cleared", basis="guaranteed", reason="r",
                          intl_solo_tag_present=False))
    with pytest.raises(actions.IntlSoloShopifyError):
        await actions.restore(db, FakeShopify(fail=True), 1, "Mistake")
    assert state_of(db) is None  # the view row is seeded; the state table was never written
    assert log_of(db)[-1]["success"] is False


# ── force_on ──────────────────────────────────────────────────────

async def test_force_on_product_outside_the_view():
    db = FakeSupabase()
    db.seed("preorder", "product_status", [{
        "product_id": 5, "status": "not_a_preorder_product", "effective_pub_date": None,
        "metadata_snapshot": {"title": "Import", "tags": [], "in_preorder_collection": False},
        "last_classified_at": None,
    }])
    out = await actions.force_on(db, FakeShopify(), 5, "UK import, ships late")
    assert out["result"] == "restricted"
    s = state_of(db, 5)
    assert (s["state"], s["basis"]) == ("restricted", "force_on")


async def test_force_on_not_allowed_on_a_preorder():
    with pytest.raises(actions.IntlSoloActionError):
        await actions.force_on(db_with(view_row()), FakeShopify(), 1, "why")


# ── reconcile ─────────────────────────────────────────────────────

def reconcile_db():
    return db_with(
        # pending: active preorder, no decision -> restrict (the backfill)
        view_row(product_id=1, state=None, basis=None, intl_solo_tag_present=False),
        # pending and already tagged (e.g. a test title) -> restrict, idempotent
        view_row(product_id=2, state=None, basis=None, intl_solo_tag_present=True),
        # restricted, tag missing -> tag_add
        view_row(product_id=3, intl_solo_tag_present=False),
        # cleared, still tagged -> tag_remove
        view_row(product_id=4, state="cleared", basis="received", reason="r"),
        # stray tag, no decision, not a preorder -> report only
        view_row(product_id=5, state=None, basis=None, classification="not_a_preorder_product",
                 in_preorder_collection=False, intl_solo_tag_present=True, pub_date=None),
        # restricted and tagged -> nothing
        view_row(product_id=6),
    )


async def test_reconcile_dry_run_changes_nothing():
    db, shop = reconcile_db(), FakeShopify()
    out = await actions.reconcile(db, shop)
    assert out["applied"] is False
    plans = {i["product_id"]: i["plan"] for i in out["plan"]}
    assert plans == {1: "restrict", 2: "restrict", 3: "tag_add", 4: "tag_remove", 5: "report_only"}
    assert shop.calls == []
    assert db.rows("preorder", "intl_solo_state") == []
    assert log_of(db) == []


async def test_reconcile_apply_needs_typed_confirmation():
    with pytest.raises(ValueError):
        await actions.reconcile(reconcile_db(), FakeShopify(), apply=True)
    with pytest.raises(ValueError):
        await actions.reconcile(reconcile_db(), FakeShopify(), apply=True, confirm="yes")


async def test_reconcile_apply():
    db, shop = reconcile_db(), FakeShopify()
    out = await actions.reconcile(db, shop, apply=True, confirm="apply", changed_by="gil")
    assert out["applied"] is True and out["failed"] == 0
    assert {c[1] for c in shop.calls if c[0] == "tagsAdd"} == {
        "gid://shopify/Product/1", "gid://shopify/Product/2", "gid://shopify/Product/3"}
    assert [c[1] for c in shop.calls if c[0] == "tagsRemove"] == ["gid://shopify/Product/4"]
    # The stray tag is never touched.
    assert not any(c[1].endswith("/5") for c in shop.calls)
    # Backfilled titles now have a decision of record.
    assert state_of(db, 1)["basis"] == "auto_preorder"
    assert state_of(db, 2)["basis"] == "auto_preorder"
    by_id = {r["product_id"]: r["result"] for r in out["results"]}
    assert by_id[5] == "skipped"


async def test_reconcile_apply_failure_records_no_decision():
    db = db_with(view_row(product_id=1, state=None, basis=None, intl_solo_tag_present=False))
    out = await actions.reconcile(db, FakeShopify(fail=True), apply=True, confirm="apply")
    assert out["failed"] == 1
    assert state_of(db, 1) is None


async def test_reconcile_can_be_limited_to_products():
    out = await actions.reconcile(reconcile_db(), FakeShopify(), product_ids=[3, 4])
    assert {i["product_id"] for i in out["plan"]} == {3, 4}


# ── Confirmed tag writes ──────────────────────────────────────────

async def test_restrict_records_confirmed_tag():
    db = db_with(view_row(state="cleared", basis="guaranteed", reason="r",
                          intl_solo_tag_present=False))
    await actions.restore(db, FakeShopify(), 1, "Mistake")
    s = state_of(db)
    assert s["tag_confirmed"] is True and s["tag_confirmed_at"]


async def test_clear_records_confirmed_removal():
    db = db_with(view_row())
    db.seed("preorder", "intl_solo_state", [{"product_id": 1, "state": "restricted",
                                              "basis": "auto_preorder", "tag_confirmed": True}])
    await actions.clear(db, FakeShopify(), 1, "guaranteed", "Confirmed")
    s = state_of(db)
    assert s["state"] == "cleared"
    assert s["tag_confirmed"] is False and s["tag_confirmed_at"]


async def test_clear_shopify_failure_leaves_confirmation_alone():
    db = db_with(view_row())
    db.seed("preorder", "intl_solo_state", [{"product_id": 1, "state": "restricted",
                                              "basis": "auto_preorder", "tag_confirmed": True,
                                              "tag_confirmed_at": "2027-01-01T00:00:00+00:00"}])
    with pytest.raises(actions.IntlSoloShopifyError):
        await actions.clear(db, FakeShopify(fail=True), 1, "guaranteed", "Confirmed")
    s = state_of(db)
    # Tag is still on in Shopify, and the record still says so.
    assert s["tag_confirmed"] is True


async def test_reconcile_records_confirmations():
    db = reconcile_db()
    db.seed("preorder", "intl_solo_state", [
        {"product_id": 3, "state": "restricted", "basis": "auto_preorder"},
        {"product_id": 4, "state": "cleared", "basis": "received", "reason": "r"},
    ])
    await actions.reconcile(db, FakeShopify(), apply=True, confirm="apply")
    assert state_of(db, 1)["tag_confirmed"] is True   # restrict (backfill)
    assert state_of(db, 3)["tag_confirmed"] is True   # tag_add
    assert state_of(db, 4)["tag_confirmed"] is False  # tag_remove
