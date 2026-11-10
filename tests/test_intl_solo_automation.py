"""
Tests for intl-solo automation (PR 3): the auto-restrict hook that runs after
reclassification, the scheduled sweep, and their safety rule: automation only
ever restricts, never clears.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from services import intl_solo_actions as actions
from services import intl_solo_service as svc
from tests.fakes import FakeSupabase
from tests.test_intl_solo_actions import FakeShopify, db_with, log_of, reconcile_db, state_of, view_row

TODAY = date(2027, 1, 12)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(actions, "today_et", lambda: TODAY)
    monkeypatch.setattr(svc, "today_et", lambda: TODAY)
    monkeypatch.delenv("INTL_SOLO_AUTO_TAG", raising=False)


# ── auto_restrict ─────────────────────────────────────────────────

async def test_new_preorder_is_restricted_automatically():
    db, shop = db_with(view_row(state=None, basis=None, intl_solo_tag_present=False)), FakeShopify()
    assert await actions.auto_restrict(db, shop, 1) == "restrict"
    s = state_of(db)
    assert (s["state"], s["basis"], s["changed_by"], s["tag_confirmed"]) == (
        "restricted", "auto_preorder", "auto", True)
    assert shop.calls[0][0] == "tagsAdd"
    entry = log_of(db)[-1]
    assert entry["action"] == "restrict" and entry["details"]["source"] == "reclassify"


async def test_restricted_title_missing_its_tag_gets_it_back():
    db = db_with(view_row(intl_solo_tag_present=False))
    db.seed("preorder", "intl_solo_state", [{"product_id": 1, "state": "restricted", "basis": "auto_preorder"}])
    shop = FakeShopify()
    assert await actions.auto_restrict(db, shop, 1) == "tag_add"
    assert state_of(db)["tag_confirmed"] is True


async def test_already_restricted_and_tagged_is_a_no_op():
    # The second products/update webhook, fired by our own tag write.
    db, shop = db_with(view_row()), FakeShopify()
    assert await actions.auto_restrict(db, shop, 1) is None
    assert shop.calls == []
    assert log_of(db) == []


async def test_automation_never_clears_or_removes_a_tag():
    # Cleared but still tagged: a person must reconcile it.
    db, shop = db_with(view_row(state="cleared", basis="received", reason="r")), FakeShopify()
    assert await actions.auto_restrict(db, shop, 1) is None
    # Stray tag on a non-preorder: reported, never removed.
    db2 = db_with(view_row(state=None, basis=None, classification="not_a_preorder_product",
                           in_preorder_collection=False, pub_date=None))
    assert await actions.auto_restrict(db2, shop, 1) is None
    assert shop.calls == []


async def test_cleared_title_is_not_re_restricted():
    # A person cleared it; reclassification must not undo that.
    db = db_with(view_row(state="cleared", basis="guaranteed", reason="r", intl_solo_tag_present=False))
    shop = FakeShopify()
    assert await actions.auto_restrict(db, shop, 1) is None
    assert shop.calls == []


async def test_non_preorder_is_left_alone():
    db = db_with(view_row(state=None, basis=None, classification="not_a_preorder_product",
                          in_preorder_collection=False, intl_solo_tag_present=False, pub_date=None))
    assert await actions.auto_restrict(db, FakeShopify(), 1) is None


async def test_shopify_refusal_records_nothing_and_does_not_raise():
    db = db_with(view_row(state=None, basis=None, intl_solo_tag_present=False))
    assert await actions.auto_restrict(db, FakeShopify(fail=True), 1) is None
    assert state_of(db) is None
    assert log_of(db)[-1]["success"] is False


async def test_errors_never_escape_into_reclassification():
    class Boom(FakeShopify):
        async def graphql(self, query, variables=None):
            raise RuntimeError("network down")
    db = db_with(view_row(state=None, basis=None, intl_solo_tag_present=False))
    assert await actions.auto_restrict(db, Boom(), 1) is None


async def test_unknown_product_is_ignored():
    assert await actions.auto_restrict(FakeSupabase(), FakeShopify(), 99) is None


async def test_kill_switch(monkeypatch):
    monkeypatch.setenv("INTL_SOLO_AUTO_TAG", "off")
    db, shop = db_with(view_row(state=None, basis=None, intl_solo_tag_present=False)), FakeShopify()
    assert await actions.auto_restrict(db, shop, 1) is None
    assert shop.calls == []


# ── sweep ─────────────────────────────────────────────────────────

async def test_sweep_applies_only_restricting_plans():
    db, shop = reconcile_db(), FakeShopify()
    db.seed("preorder", "intl_solo_state", [{"product_id": 3, "state": "restricted", "basis": "auto_preorder"}])
    out = await actions.sweep(db, shop)
    added = {c[1] for c in shop.calls if c[0] == "tagsAdd"}
    assert added == {"gid://shopify/Product/1", "gid://shopify/Product/2", "gid://shopify/Product/3"}
    assert not any(c[0] == "tagsRemove" for c in shop.calls)  # product 4 is left for a person
    needs = {i["product_id"]: i["plan"] for i in out["needs_person"]}
    assert needs == {4: "tag_remove", 5: "report_only"}
    by_id = {r["product_id"]: r["result"] for r in out["results"]}
    assert by_id[4] == "skipped" and by_id[5] == "skipped"
    assert state_of(db, 1)["changed_by"] == "auto"


async def test_sweep_dry_run_changes_nothing():
    db, shop = reconcile_db(), FakeShopify()
    out = await actions.sweep(db, shop, dry_run=True)
    assert out["applied"] is False
    assert shop.calls == []
    assert {i["product_id"] for i in out["needs_person"]} == {4, 5}


async def test_sweep_kill_switch(monkeypatch):
    monkeypatch.setenv("INTL_SOLO_AUTO_TAG", "off")
    shop = FakeShopify()
    out = await actions.sweep(reconcile_db(), shop)
    assert out["disabled"] is True
    assert shop.calls == []


async def test_manual_reconcile_still_applies_everything():
    # `only` is opt-in; the endpoint's behavior is unchanged.
    db, shop = reconcile_db(), FakeShopify()
    out = await actions.reconcile(db, shop, apply=True, confirm="apply")
    assert any(c[0] == "tagsRemove" for c in shop.calls)
    assert out["failed"] == 0


# ── the hook in reclassify_single_product ─────────────────────────

async def test_reclassification_calls_auto_restrict(monkeypatch):
    from services import reclassification_service as rs

    async def fake_build(product_id, client):
        return SimpleNamespace(product_id=product_id)

    monkeypatch.setattr(rs, "build_product_metadata_from_shopify", fake_build)
    monkeypatch.setattr(rs, "classify_and_persist_product", lambda **kw: SimpleNamespace(
        status="active_preorder", anomaly_type=None, effective_pub_date=None))
    seen = []

    async def fake_auto(supabase, client, product_id, source="reclassify"):
        seen.append(product_id)
        return None

    monkeypatch.setattr(actions, "auto_restrict", fake_auto)
    out = await rs.reclassify_single_product(supabase=object(), shopify_client=object(), product_id=42)
    assert seen == [42]
    assert out.status == "active_preorder"
