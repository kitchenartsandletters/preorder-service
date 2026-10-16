"""
Tests for services/cleanup_guard.py and its wiring into the cleanup routes.

A hold (preorder.preorder_hold) is a deliberate decision to keep a past-dated
title an active preorder. What is held is Preorder collection membership, which
drives the rewards system and the preorder copy — so cleaning the title up ends
the hold early. The cleanup routes read Shopify state only, so nothing stopped
that before this guard.

The guard used to INFER a hold from (active_preorder AND past pub date AND no
arrival). That inference was retired; it now reads the hold record.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from services.cleanup_guard import check_active_hold

from services.hold_service import today_et

YESTERDAY = (today_et() - timedelta(days=1)).isoformat()
TODAY = today_et().isoformat()
TOMORROW = (today_et() + timedelta(days=1)).isoformat()

HELD_ID = 111
PRODUCT_TITLE = "Delayed Import Title"


def seed_hold(sb, product_id=HELD_ID, hold_until=None, reason="event", note=None):
    sb.seed("preorder", "preorder_hold", [{
        "product_id": product_id,
        "hold_until": (hold_until or TOMORROW),
        "reason": reason,
        "note": note,
        "created_by": "tester",
    }])


# ---- the rule --------------------------------------------------------------

def test_live_hold_is_detected(fake_supabase):
    seed_hold(fake_supabase, note="signing with the author")

    hold = check_active_hold(fake_supabase, HELD_ID)

    assert hold is not None
    assert hold["reason"] == "preorder_hold"
    assert hold["hold_until"] == TOMORROW
    assert hold["hold_reason"] == "event"
    assert "signing with the author" in hold["message"]
    assert "force=true" in hold["resolution"]


def test_hold_ending_today_still_blocks(fake_supabase):
    """hold_until is the last held day, inclusive, in ET."""
    seed_hold(fake_supabase, hold_until=TODAY)
    assert check_active_hold(fake_supabase, HELD_ID) is not None


def test_expired_hold_does_not_block(fake_supabase):
    seed_hold(fake_supabase, hold_until=YESTERDAY)
    assert check_active_hold(fake_supabase, HELD_ID) is None


def test_no_hold_does_not_block(fake_supabase):
    assert check_active_hold(fake_supabase, HELD_ID) is None


def test_a_past_dated_title_without_a_hold_is_no_longer_inferred_as_held(fake_supabase):
    """
    The retired rule: active_preorder + past pub date + no arrival meant held.
    Cleanup is now the correct action for such a title, so it must not block.
    """
    fake_supabase.seed("preorder", "product_status", [
        {"product_id": HELD_ID, "status": "active_preorder", "effective_pub_date": YESTERDAY}
    ])
    assert check_active_hold(fake_supabase, HELD_ID) is None


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
    seed_hold(fake_supabase)

    resp = cleanup_client.post(path)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reason"] == "preorder_hold"
    assert detail["product_id"] == HELD_ID


@pytest.mark.parametrize("path", [
    "/admin/preorders/cleanup/collection/111",
    "/admin/preorders/cleanup/unpublish/111",
    "/admin/preorders/cleanup/full/111",
])
def test_force_overrides_the_hold(cleanup_client, fake_supabase, path):
    seed_hold(fake_supabase)

    resp = cleanup_client.post(f"{path}?force=true")

    assert resp.status_code == 200


@pytest.mark.parametrize("path", [
    "/admin/preorders/cleanup/collection/111",
    "/admin/preorders/cleanup/unpublish/111",
    "/admin/preorders/cleanup/full/111",
])
def test_unheld_title_proceeds_normally(cleanup_client, fake_supabase, path):
    seed_hold(fake_supabase, hold_until=YESTERDAY)  # expired: no longer protects

    resp = cleanup_client.post(path)

    assert resp.status_code == 200


def test_description_cleanup_is_not_guarded(cleanup_client, fake_supabase):
    """Description cleanup rewrites marketing copy only; it changes no status."""
    seed_hold(fake_supabase)

    resp = cleanup_client.post("/admin/preorders/cleanup/description/111")

    assert resp.status_code == 200
