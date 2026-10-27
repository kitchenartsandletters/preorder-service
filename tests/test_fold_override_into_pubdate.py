"""
Tests for scripts/fold_override_into_pubdate.py (rev2 Move 1d).

The fold sets pub_date := override so pub_date can become the single source of
truth. The behaviours that matter are the safety ones: dry run writes nothing,
already-folded products are skipped, every write carries compareDigest, and each
write is recorded in pubdate_history as override_migration with its direction.
"""

import importlib.util
import pathlib
from datetime import date, timedelta

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "fold_script", REPO / "scripts" / "fold_override_into_pubdate.py"
)
fold = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fold)

LATER = (date.today() + timedelta(days=40)).isoformat()
EARLIER = (date.today() - timedelta(days=5)).isoformat()
PUB = (date.today() + timedelta(days=10)).isoformat()


class FakeShopify:
    def __init__(self, overrides, pub_dates, fail_on=()):
        self.overrides, self.pub_dates, self.fail_on = overrides, pub_dates, fail_on
        self.writes = []

    async def graphql(self, query=None, variables=None, **kw):
        if "OverrideValue" in query:
            pid = int(variables["id"].split("/")[-1])
            return {"product": {"id": variables["id"], "title": f"T{pid}",
                                "metafield": {"value": self.overrides.get(pid)}}}
        if "PubDateState" in query:
            pid = int(variables["id"].split("/")[-1])
            value = self.pub_dates.get(pid)
            return {
                "product": {"id": variables["id"], "title": f"T{pid}",
                            "metafield": {"id": "m", "type": "date", "value": value,
                                          "compareDigest": f"digest-{pid}"} if value else None},
                "metafieldDefinitions": {"nodes": [{"type": {"name": "date"}}]},
            }
        if "SetPubDate" in query:
            mf = variables["metafields"][0]
            pid = int(mf["ownerId"].split("/")[-1])
            if pid in self.fail_on:
                return {"metafieldsSet": {"metafields": [], "userErrors": [
                    {"code": "STALE_OBJECT", "message": "compareDigest does not match"}]}}
            self.writes.append(mf)
            return {"metafieldsSet": {"metafields": [{"value": mf["value"]}], "userErrors": []}}
        raise AssertionError(query[:60])

    async def close(self):
        pass


def seed(fake_supabase, rows):
    fake_supabase.seed("preorder", "product_status", rows)


def status_row(pid, pub, override, status="active_preorder", effective=None):
    return {"product_id": pid, "status": status,
            "effective_pub_date": effective or override,
            "metadata_snapshot": {"title": f"T{pid}", "pub_date": pub, "override_date": override}}


@pytest.fixture
def wired(fake_supabase, monkeypatch):
    def _wire(shopify):
        monkeypatch.setattr(fold, "get_supabase_client", lambda: fake_supabase)
        monkeypatch.setattr(fold, "ShopifyClient", lambda: shopify)
        return fake_supabase, shopify
    return _wire


def test_candidates_are_only_products_with_an_override(fake_supabase):
    seed(fake_supabase, [status_row(1, PUB, LATER),
                         {"product_id": 2, "status": "active_preorder", "effective_pub_date": PUB,
                          "metadata_snapshot": {"title": "no override", "pub_date": PUB}}])
    assert [c["product_id"] for c in fold.candidate_products(fake_supabase)] == [1]


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(wired, capsys):
    supabase, shopify = wired(FakeShopify({1: LATER}, {1: PUB}))
    seed(supabase, [status_row(1, PUB, LATER)])

    rc = await fold.run(apply=False, product_ids=None)

    assert rc == 0
    assert shopify.writes == []
    assert supabase.rows("preorder", "pubdate_history") == []
    assert "DRY RUN" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_apply_writes_with_digest_and_records_history(wired):
    supabase, shopify = wired(FakeShopify({1: LATER}, {1: PUB}))
    seed(supabase, [status_row(1, PUB, LATER)])

    await fold.run(apply=True, product_ids=None)

    assert len(shopify.writes) == 1
    written = shopify.writes[0]
    assert written["value"] == LATER
    assert written["compareDigest"] == "digest-1"
    assert written["key"] == "pub_date"

    history = supabase.rows("preorder", "pubdate_history")
    assert len(history) == 1
    row = history[0]
    assert row["change_source"] == "override_migration"
    assert row["old_effective_pub_date"] == PUB
    assert row["new_effective_pub_date"] == LATER
    assert row["metadata"]["direction"] == "later"
    assert row["metadata"]["effective_date_unchanged"] is True
    assert row["metadata"]["migration_run"]


@pytest.mark.asyncio
async def test_already_folded_product_is_skipped(wired):
    """Idempotency: a second run must not rewrite or re-record."""
    supabase, shopify = wired(FakeShopify({1: LATER}, {1: LATER}))
    seed(supabase, [status_row(1, LATER, LATER)])

    await fold.run(apply=True, product_ids=None)

    assert shopify.writes == []
    assert supabase.rows("preorder", "pubdate_history") == []


@pytest.mark.asyncio
async def test_direction_is_recorded_for_an_earlier_override(wired):
    """The 3 conflicts: the storefront date moves earlier, deliberately."""
    supabase, shopify = wired(FakeShopify({1: EARLIER}, {1: PUB}))
    seed(supabase, [status_row(1, PUB, EARLIER)])

    await fold.run(apply=True, product_ids=None)

    assert supabase.rows("preorder", "pubdate_history")[0]["metadata"]["direction"] == "earlier"


@pytest.mark.asyncio
async def test_missing_pub_date_is_direction_set(wired):
    supabase, shopify = wired(FakeShopify({1: LATER}, {1: None}))
    seed(supabase, [status_row(1, None, LATER)])

    await fold.run(apply=True, product_ids=None)

    row = supabase.rows("preorder", "pubdate_history")[0]
    assert row["metadata"]["direction"] == "set"
    assert row["old_effective_pub_date"] is None


@pytest.mark.asyncio
async def test_a_digest_conflict_fails_that_product_only(wired, capsys):
    supabase, shopify = wired(FakeShopify({1: LATER, 2: LATER}, {1: PUB, 2: PUB}, fail_on=(1,)))
    seed(supabase, [status_row(1, PUB, LATER), status_row(2, PUB, LATER)])

    rc = await fold.run(apply=True, product_ids=None)

    assert rc == 1
    assert [w["ownerId"].split("/")[-1] for w in shopify.writes] == ["2"]
    assert len(supabase.rows("preorder", "pubdate_history")) == 1
    assert "FAILED 1" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_product_ids_limits_the_run(wired):
    supabase, shopify = wired(FakeShopify({1: LATER, 2: LATER}, {1: PUB, 2: PUB}))
    seed(supabase, [status_row(1, PUB, LATER), status_row(2, PUB, LATER)])

    await fold.run(apply=True, product_ids=[2])

    assert [w["ownerId"].split("/")[-1] for w in shopify.writes] == ["2"]
