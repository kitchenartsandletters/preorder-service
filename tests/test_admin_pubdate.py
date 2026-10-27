"""
Tests for the sanctioned pub-date editor (rev2 Move 1c).

The behaviours worth pinning are the ones that protect data:

- the metafield TYPE is read, never guessed;
- every write carries compareDigest, so a concurrent edit fails loudly (409)
  instead of clobbering somebody;
- a write intent is recorded BEFORE the Shopify write, so the webhook Shopify
  sends back for our own edit is labelled ui_change rather than
  shopify_direct_edit;
- if the Shopify write fails, the intent is closed, or it would sit open and
  mislabel an unrelated later change.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services import pubdate_service
from services.pubdate_service import PubDateConflict, PubDateError

PRODUCT = 7300376330373
NEW_DATE = (date.today() + timedelta(days=30)).isoformat()


class FakeShopify:
    """Minimal Admin API stand-in recording the calls made."""

    def __init__(self, metafield=None, definition_type="date", user_errors=None, product=True):
        self.metafield = metafield
        self.definition_type = definition_type
        self.user_errors = user_errors or []
        self.product = product
        self.calls = []

    async def graphql(self, query=None, variables=None, **kwargs):
        self.calls.append({"query": query, "variables": variables})
        if "PubDateState" in query:
            if not self.product:
                return {"product": None}
            return {
                "product": {"id": "gid://shopify/Product/1", "title": "A Title",
                            "metafield": self.metafield},
                "metafieldDefinitions": {
                    "nodes": [{"type": {"name": self.definition_type}}] if self.definition_type else []
                },
            }
        if "SetPubDate" in query:
            return {"metafieldsSet": {
                "metafields": [{"value": variables["metafields"][0]["value"]}],
                "userErrors": self.user_errors,
            }}
        raise AssertionError(f"unexpected query: {query[:60]}")

    async def close(self):
        pass


EXISTING_METAFIELD = {"id": "gid://shopify/Metafield/9", "type": "date",
                      "value": "2026-10-13", "compareDigest": "digest-abc"}


# ---- service ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_state_returns_value_type_and_digest():
    state = await pubdate_service.read_pub_date_state(FakeShopify(EXISTING_METAFIELD), PRODUCT)
    assert state["value"] == "2026-10-13"
    assert state["metafield_type"] == "date"
    assert state["compare_digest"] == "digest-abc"
    assert state["exists"] is True


@pytest.mark.asyncio
async def test_type_falls_back_to_the_definition_when_no_value_yet():
    """First write for a product must still use the right type."""
    state = await pubdate_service.read_pub_date_state(
        FakeShopify(metafield=None, definition_type="date"), PRODUCT
    )
    assert state["metafield_type"] == "date"
    assert state["compare_digest"] is None
    assert state["exists"] is False


@pytest.mark.asyncio
async def test_write_sends_the_digest_and_the_type():
    client = FakeShopify(EXISTING_METAFIELD)
    await pubdate_service.set_pub_date(
        client, PRODUCT, date(2026, 11, 3), metafield_type="date", compare_digest="digest-abc"
    )
    sent = client.calls[-1]["variables"]["metafields"][0]
    assert sent["compareDigest"] == "digest-abc"
    assert sent["type"] == "date"
    assert sent["value"] == "2026-11-03"
    assert sent["namespace"] == "custom" and sent["key"] == "pub_date"


@pytest.mark.asyncio
async def test_write_uses_the_shops_actual_type_not_a_default():
    """
    The type must come from Shopify. A hard-coded "date" would pass on this
    shop today and silently break one configured differently.
    """
    odd = {"id": "gid://shopify/Metafield/9", "type": "single_line_text_field",
           "value": "2026-10-13", "compareDigest": "d"}
    client = FakeShopify(odd, definition_type="single_line_text_field")

    state = await pubdate_service.read_pub_date_state(client, PRODUCT)
    await pubdate_service.set_pub_date(
        client, PRODUCT, date(2026, 11, 3),
        metafield_type=state["metafield_type"], compare_digest=state["compare_digest"],
    )

    assert client.calls[-1]["variables"]["metafields"][0]["type"] == "single_line_text_field"


@pytest.mark.asyncio
async def test_unknown_type_is_refused_rather_than_guessed():
    client = FakeShopify(metafield=None, definition_type=None)
    with pytest.raises(PubDateError, match="refusing to guess"):
        await pubdate_service.set_pub_date(
            client, PRODUCT, date(2026, 11, 3), metafield_type=None, compare_digest=None
        )


@pytest.mark.asyncio
async def test_digest_mismatch_raises_conflict():
    client = FakeShopify(EXISTING_METAFIELD, user_errors=[
        {"code": "STALE_OBJECT", "message": "compareDigest does not match the persisted value"}
    ])
    with pytest.raises(PubDateConflict):
        await pubdate_service.set_pub_date(
            client, PRODUCT, date(2026, 11, 3), metafield_type="date", compare_digest="stale"
        )


# ---- endpoint --------------------------------------------------------------

@pytest.fixture
def client(fake_supabase, monkeypatch):
    from main import app
    from routes import admin_pubdate

    shopify = FakeShopify(EXISTING_METAFIELD)

    async def fake_reclassify(*, supabase, shopify_client, product_id, **kw):
        return SimpleNamespace(status="active_preorder")

    monkeypatch.setattr(admin_pubdate, "reclassify_single_product", fake_reclassify)
    app.dependency_overrides[admin_pubdate.require_admin_token] = lambda: True
    app.dependency_overrides[admin_pubdate.get_supabase_client] = lambda: fake_supabase
    app.dependency_overrides[admin_pubdate.get_shopify_client] = lambda: shopify

    test_client = TestClient(app)
    test_client.shopify = shopify
    yield test_client
    app.dependency_overrides.clear()


def put(client, **body):
    payload = {"pub_date": NEW_DATE, "reason": "publisher moved it", "changed_by": "gil"}
    payload.update(body)
    return client.put(f"/admin/preorders/{PRODUCT}/pub-date", json=payload)


def test_successful_write_records_an_intent_first(client, fake_supabase):
    resp = put(client)

    assert resp.status_code == 200
    assert resp.json()["result"] == "updated"
    assert resp.json()["previous_pub_date"] == "2026-10-13"

    intents = fake_supabase.rows("preorder", "pubdate_write_intent")
    assert len(intents) == 1
    assert intents[0]["new_date"] == NEW_DATE
    assert intents[0]["changed_by"] == "gil"
    assert intents[0]["reason"] == "publisher moved it"
    assert intents[0].get("consumed_at") is None  # the orchestrator claims it


def test_no_change_when_the_date_already_matches(client):
    resp = put(client, pub_date="2026-10-13")
    assert resp.status_code == 200
    assert resp.json()["result"] == "no_change"


def test_conflict_returns_409_and_closes_the_intent(client, fake_supabase):
    client.shopify.user_errors = [
        {"code": "STALE_OBJECT", "message": "compareDigest does not match"}
    ]

    resp = put(client)

    assert resp.status_code == 409
    intents = fake_supabase.rows("preorder", "pubdate_write_intent")
    assert intents and intents[0]["consumed_at"] is not None, (
        "a failed write must close its intent, or it would mislabel a later change"
    )


def test_missing_product_returns_404(client):
    client.shopify.product = False
    assert put(client).status_code == 404


def test_response_warns_about_the_stale_description(client):
    assert "description" in put(client).json()["description_warning"].lower()


def test_history_endpoint_flags_folds_and_reconstructed_rows(client, fake_supabase):
    fake_supabase.seed("preorder", "pubdate_history", [
        {"product_id": PRODUCT, "old_effective_pub_date": "2026-10-13",
         "new_effective_pub_date": "2026-10-06", "change_source": "override_migration",
         "changed_at": "2026-10-20T00:00:00+00:00", "metadata": {"direction": "earlier"}},
        {"product_id": PRODUCT, "old_effective_pub_date": None,
         "new_effective_pub_date": "2026-01-01", "change_source": "tag_history_backfill",
         "changed_at": "2026-10-19T00:00:00+00:00", "metadata": {"reconstructed": True}},
        {"product_id": PRODUCT, "old_effective_pub_date": "2026-10-06",
         "new_effective_pub_date": NEW_DATE, "change_source": "ui_change",
         "changed_at": "2026-10-21T00:00:00+00:00",
         "metadata": {"changed_by": "gil", "reason": "publisher moved it"}},
    ])

    body = client.get(f"/admin/preorders/{PRODUCT}/pub-date-history").json()

    assert body["count"] == 3
    newest = body["history"][0]
    assert newest["change_source"] == "ui_change"
    assert newest["changed_by"] == "gil" and newest["reason"] == "publisher moved it"
    by_source = {r["change_source"]: r for r in body["history"]}
    assert by_source["override_migration"]["is_fold"] is True
    assert by_source["tag_history_backfill"]["reconstructed"] is True
    assert by_source["ui_change"]["reconstructed"] is False
