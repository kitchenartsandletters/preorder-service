"""
Tests for services/cleanup_guard.py and its wiring into the cleanup routes.

A delayed-import hold is a past-dated title the engine deliberately keeps
`active_preorder` because stock never arrived and commitments are open. Cleaning
it up removes it from the Preorder collection, which flips it to
`historical_preorder` and ends the hold early. The cleanup routes read Shopify
state only, so nothing stopped that before this guard.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from services.cleanup_guard import check_delayed_import_hold

YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
TODAY = date.today().isoformat()
NEXT_MONTH = (date.today() + timedelta(days=30)).isoformat()

HELD_ID = 111
PRODUCT_TITLE = "Delayed Import Title"


def seed_status(sb, product_id, status, effective_pub_date):
    sb.seed("preorder", "product_status", [
        {"product_id": product_id, "status": status, "effective_pub_date": effective_pub_date}
    ])


def seed_arrival(sb, product_id):
    sb.seed("preorder", "inventory_arrival", [{"product_id": product_id}])


# ---- the rule --------------------------------------------------------------

def test_past_dated_active_with_no_arrival_is_held(fake_supabase):
    seed_status(fake_supabase, HELD_ID, "active_preorder", YESTERDAY)

    hold = check_delayed_import_hold(fake_supabase, HELD_ID)

    assert hold is not None
    assert hold["reason"] == "delayed_import_hold"
    assert hold["effective_pub_date"] == YESTERDAY
    assert "force=true" in hold["resolution"]


def test_pub_day_is_not_a_hold(fake_supabase):
    seed_status(fake_supabase, HELD_ID, "active_preorder", TODAY)
    assert check_delayed_import_hold(fake_supabase, HELD_ID) is None


def test_future_dated_active_is_not_a_hold(fake_supabase):
    seed_status(fake_supabase, HELD_ID, "active_preorder", NEXT_MONTH)
    assert check_delayed_import_hold(fake_supabase, HELD_ID) is None


def test_arrival_record_ends_the_hold(fake_supabase):
    seed_status(fake_supabase, HELD_ID, "active_preorder", YESTERDAY)
    seed_arrival(fake_supabase, HELD_ID)
    assert check_delayed_import_hold(fake_supabase, HELD_ID) is None


@pytest.mark.parametrize("status", [
    "historical_preorder",
    "early_stock_arrival",
    "anomaly_stale_collection",
    "not_a_preorder_product",
])
def test_other_statuses_are_never_held(fake_supabase, status):
    """anomaly_stale_collection especially: cleanup is exactly the right action there."""
    seed_status(fake_supabase, HELD_ID, status, YESTERDAY)
    assert check_delayed_import_hold(fake_supabase, HELD_ID) is None


def test_unknown_product_does_not_block_cleanup(fake_supabase):
    assert check_delayed_import_hold(fake_supabase, 999) is None


def test_missing_effective_pub_date_does_not_block_cleanup(fake_supabase):
    seed_status(fake_supabase, HELD_ID, "active_preorder", None)
    assert check_delayed_import_hold(fake_supabase, HELD_ID) is None


# ---- route wiring ----------------------------------------------------------

@pytest.fixture
def cleanup_client(fake_supabase, monkeypatch):
    """TestClient with admin auth bypassed, the fake DB injected, Shopify stubbed."""
    from main import app
    from routes import admin_cleanup

    monkeypatch.setattr(admin_cleanup, "supabase", fake_supabase)
    monkeypatch.setattr(admin_cleanup, "ShopifyClient", lambda: AsyncMock())

    state = SimpleNamespace(
        title=PRODUCT_TITLE,
        in_preorder_collection=True,
        published_to_catch_all=True,
        preorder_collection_gid="gid://shopify/Collection/1",
        description_analysis=SimpleNamespace(
            needs_cleaning=False, status="clean", cleaned_html="<p>x</p>"
        ),
    )
    monkeypatch.setattr(admin_cleanup, "fetch_cleanup_state", AsyncMock(return_value=state))
    monkeypatch.setattr(admin_cleanup, "remove_from_preorder_collection", AsyncMock(return_value=[]))
    monkeypatch.setattr(admin_cleanup, "unpublish_from_catch_all", AsyncMock(return_value=[]))
    monkeypatch.setattr(admin_cleanup, "log_cleanup_action", lambda **kw: None)

    app.dependency_overrides[admin_cleanup.require_admin_token] = lambda: True
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.parametrize("path", [
    "/admin/preorders/cleanup/collection/111",
    "/admin/preorders/cleanup/unpublish/111",
    "/admin/preorders/cleanup/full/111",
])
def test_held_title_is_refused_with_409(cleanup_client, fake_supabase, path):
    seed_status(fake_supabase, HELD_ID, "active_preorder", YESTERDAY)

    resp = cleanup_client.post(path)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reason"] == "delayed_import_hold"
    assert detail["product_id"] == HELD_ID


@pytest.mark.parametrize("path", [
    "/admin/preorders/cleanup/collection/111",
    "/admin/preorders/cleanup/unpublish/111",
    "/admin/preorders/cleanup/full/111",
])
def test_force_overrides_the_hold(cleanup_client, fake_supabase, path):
    seed_status(fake_supabase, HELD_ID, "active_preorder", YESTERDAY)

    resp = cleanup_client.post(f"{path}?force=true")

    assert resp.status_code == 200


@pytest.mark.parametrize("path", [
    "/admin/preorders/cleanup/collection/111",
    "/admin/preorders/cleanup/unpublish/111",
    "/admin/preorders/cleanup/full/111",
])
def test_unheld_title_proceeds_normally(cleanup_client, fake_supabase, path):
    seed_status(fake_supabase, HELD_ID, "anomaly_stale_collection", YESTERDAY)

    resp = cleanup_client.post(path)

    assert resp.status_code == 200


def test_description_cleanup_is_not_guarded(cleanup_client, fake_supabase):
    """Description cleanup rewrites marketing copy only; it changes no status."""
    seed_status(fake_supabase, HELD_ID, "active_preorder", YESTERDAY)

    resp = cleanup_client.post("/admin/preorders/cleanup/description/111")

    assert resp.status_code == 200
