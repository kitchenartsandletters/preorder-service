"""
Tests for services/hold_service.py and routes/admin_holds.py.

A hold is a deliberate decision to keep a past-dated title an active preorder,
replacing the engine's old inference. The rule that matters most is expiry:
`hold_until` is the LAST held day, inclusive, in America/New_York. The SQL view
applies the same expression, so the dashboard and the backend agree; these tests
pin the Python half.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from services import hold_service

TOMORROW = date.today() + timedelta(days=1)
LAST_WEEK = date.today() - timedelta(days=7)
PRODUCT = 7301225480325


def seed_hold(sb, product_id=PRODUCT, hold_until=None, reason="event", note=None):
    sb.seed("preorder", "preorder_hold", [{
        "product_id": product_id,
        "hold_until": (hold_until or TOMORROW).isoformat(),
        "reason": reason,
        "note": note,
        "created_by": "tester",
        "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-01T00:00:00+00:00",
    }])


# ---- expiry rule -----------------------------------------------------------

def test_hold_until_today_et_is_still_active():
    """Inclusive last day: a hold until today is live all of today."""
    today = hold_service.today_et()
    assert hold_service.is_active({"hold_until": today.isoformat()}, as_of=today) is True


def test_hold_expires_the_day_after():
    today = hold_service.today_et()
    yesterday = (today - timedelta(days=1)).isoformat()
    assert hold_service.is_active({"hold_until": yesterday}, as_of=today) is False


def test_no_hold_is_not_active():
    assert hold_service.is_active(None) is False
    assert hold_service.is_active({}) is False
    assert hold_service.is_active({"hold_until": None}) is False


def test_today_et_is_eastern_not_utc():
    """Expiry must not use the container clock; the dashboard uses ET."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    assert hold_service.ET == ZoneInfo("America/New_York")
    expected = datetime.now(timezone.utc).astimezone(hold_service.ET).date()
    assert hold_service.today_et() == expected


# ---- service ---------------------------------------------------------------

def test_has_active_hold_reads_the_row(fake_supabase):
    assert hold_service.has_active_hold(fake_supabase, PRODUCT) is False
    seed_hold(fake_supabase)
    assert hold_service.has_active_hold(fake_supabase, PRODUCT) is True


def test_expired_hold_is_not_active(fake_supabase):
    seed_hold(fake_supabase, hold_until=LAST_WEEK)
    assert hold_service.has_active_hold(fake_supabase, PRODUCT) is False
    # ...but the row is still readable, for the recently-expired alert.
    assert hold_service.get_hold(fake_supabase, PRODUCT) is not None


def test_set_hold_round_trips(fake_supabase):
    hold_service.set_hold(
        fake_supabase, product_id=PRODUCT, hold_until=TOMORROW,
        reason="awaiting_stock", note="ETA from publisher", created_by="gil",
    )
    stored = hold_service.get_hold(fake_supabase, PRODUCT)
    assert stored["hold_until"] == TOMORROW.isoformat()
    assert stored["reason"] == "awaiting_stock"
    assert stored["note"] == "ETA from publisher"
    assert hold_service.has_active_hold(fake_supabase, PRODUCT) is True


def test_set_hold_replaces_rather_than_duplicates(fake_supabase):
    hold_service.set_hold(fake_supabase, PRODUCT, TOMORROW, "event")
    hold_service.set_hold(fake_supabase, PRODUCT, TOMORROW + timedelta(days=3), "awaiting_stock")

    rows = fake_supabase.rows("preorder", "preorder_hold")
    assert len(rows) == 1
    assert rows[0]["reason"] == "awaiting_stock"


def test_set_hold_rejects_unknown_reason(fake_supabase):
    with pytest.raises(ValueError, match="reason must be one of"):
        hold_service.set_hold(fake_supabase, PRODUCT, TOMORROW, "because")


def test_set_hold_rejects_a_date_already_past(fake_supabase):
    """An already-expired hold is inert; refusing beats silently doing nothing."""
    with pytest.raises(ValueError, match="already past"):
        hold_service.set_hold(fake_supabase, PRODUCT, LAST_WEEK, "event")
    assert fake_supabase.rows("preorder", "preorder_hold") == []


def test_clear_hold(fake_supabase):
    seed_hold(fake_supabase)
    assert hold_service.clear_hold(fake_supabase, PRODUCT) is True
    assert hold_service.get_hold(fake_supabase, PRODUCT) is None
    assert hold_service.clear_hold(fake_supabase, PRODUCT) is False


def test_list_holds_excludes_expired_by_default(fake_supabase):
    seed_hold(fake_supabase, product_id=1, hold_until=TOMORROW)
    seed_hold(fake_supabase, product_id=2, hold_until=LAST_WEEK)

    active = hold_service.list_holds(fake_supabase)
    assert [h["product_id"] for h in active] == [1]
    assert active[0]["hold_active"] is True

    everything = hold_service.list_holds(fake_supabase, include_expired=True)
    assert sorted(h["product_id"] for h in everything) == [1, 2]
    assert {h["product_id"]: h["hold_active"] for h in everything} == {1: True, 2: False}


# ---- endpoints -------------------------------------------------------------

@pytest.fixture
def holds_client(fake_supabase, monkeypatch):
    from main import app
    from routes import admin_holds

    app.dependency_overrides[admin_holds.require_admin_token] = lambda: True
    app.dependency_overrides[admin_holds.get_supabase_client] = lambda: fake_supabase
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_put_and_get_hold(holds_client):
    resp = holds_client.put(
        f"/admin/preorders/holds/{PRODUCT}",
        json={"hold_until": TOMORROW.isoformat(), "reason": "event",
              "note": "signing with the author", "created_by": "gil"},
    )
    assert resp.status_code == 200
    assert resp.json()["hold_active"] is True

    got = holds_client.get(f"/admin/preorders/holds/{PRODUCT}")
    assert got.status_code == 200
    assert got.json()["note"] == "signing with the author"
    assert got.json()["hold_active"] is True


def test_put_rejects_past_date_with_400(holds_client):
    resp = holds_client.put(
        f"/admin/preorders/holds/{PRODUCT}",
        json={"hold_until": LAST_WEEK.isoformat(), "reason": "event"},
    )
    assert resp.status_code == 400
    assert "already past" in resp.json()["detail"]


def test_put_rejects_unknown_reason_with_400(holds_client):
    resp = holds_client.put(
        f"/admin/preorders/holds/{PRODUCT}",
        json={"hold_until": TOMORROW.isoformat(), "reason": "nope"},
    )
    assert resp.status_code == 400


def test_get_unknown_hold_is_404(holds_client):
    assert holds_client.get("/admin/preorders/holds/999").status_code == 404


def test_delete_hold(holds_client, fake_supabase):
    seed_hold(fake_supabase)
    assert holds_client.delete(f"/admin/preorders/holds/{PRODUCT}").status_code == 200
    assert holds_client.delete(f"/admin/preorders/holds/{PRODUCT}").status_code == 404


def test_list_endpoint_reports_today_et_and_expired_flag(holds_client, fake_supabase):
    seed_hold(fake_supabase, product_id=1, hold_until=TOMORROW)
    seed_hold(fake_supabase, product_id=2, hold_until=LAST_WEEK)

    body = holds_client.get("/admin/preorders/holds").json()
    assert body["count"] == 1
    assert body["today_et"] == hold_service.today_et().isoformat()

    with_expired = holds_client.get("/admin/preorders/holds?include_expired=true").json()
    assert with_expired["count"] == 2
