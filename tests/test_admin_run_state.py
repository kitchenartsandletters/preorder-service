"""
Tests for GET /admin/preorders/run-state.

The dashboard (admin-dashboard PR #129) was merged against this route before it
existed, and it consumes the view's rows unchanged. So the contract worth
pinning is not the SQL — it is the SHAPE:

- the path is exactly /admin/preorders/run-state,
- the body is a bare JSON array, not an envelope,
- column names reach the client untouched,
- it is behind X-Admin-Token like every sibling read endpoint.

Each test here was written against a change that would break the dashboard
silently rather than loudly.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeSupabase

PATH = "/admin/preorders/run-state"

SEPTIME = 6598890029189
CONTRA = 5238867361925

# The live rows, as vw_run_state returns them on 2026-11-24.
SEPTIME_ROW = {
    "product_id": SEPTIME,
    "title": "Septime (English)",
    "isbn": "9781838662202",
    "classification": "active_preorder",
    "effective_pub_date": "2026-12-16",
    "has_prior_closed_run": True,
    "lifecycle_closed_at": "2026-03-31T15:05:04+00:00",
    "current_run_started_at": "2026-03-31T15:05:04+00:00",
    "prior_run_presale_total": 0,
    "prior_run_reported": True,
    "current_run_open_lines": 17,
    "current_run_open_units": 17,
    "current_run_open_orders": 17,
    "current_run_reported": False,
    "orphan_lines": 0,
    "inventory_unclamped": -17,
}

CONTRA_ROW = {
    "product_id": CONTRA,
    "title": "A Very Serious Cookbook: Contra Wildair",
    "isbn": "9780714876023",
    "classification": "active_preorder",
    "effective_pub_date": "2026-12-07",
    "has_prior_closed_run": False,
    "lifecycle_closed_at": None,
    "current_run_started_at": None,
    "prior_run_presale_total": None,
    "prior_run_reported": None,
    "current_run_open_lines": 87,
    "current_run_open_units": 88,
    "current_run_open_orders": 87,
    "current_run_reported": False,
    "orphan_lines": 0,
    "inventory_unclamped": -88,
}


@pytest.fixture
def fake_supabase():
    db = FakeSupabase()
    db.seed("preorder", "vw_run_state", [SEPTIME_ROW, CONTRA_ROW])
    return db


@pytest.fixture
def client(fake_supabase, monkeypatch):
    from main import app
    from routes import admin_preorders

    # admin_preorders builds its Supabase client at import (DOCS_STATUS
    # Landmine 10), so it is patched rather than overridden as a dependency.
    monkeypatch.setattr(admin_preorders, "supabase", fake_supabase)
    app.dependency_overrides[admin_preorders.require_admin_token] = lambda: True

    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Shape — what the dashboard actually depends on
# --------------------------------------------------------------------------

def test_route_is_mounted_at_the_path_the_dashboard_calls():
    """The frontend hard-codes this path in RUN_STATE_PATH."""
    from main import app

    assert PATH in app.openapi()["paths"]
    assert "get" in app.openapi()["paths"][PATH]


def test_body_is_a_bare_array_not_an_envelope(client):
    """Wrapping the rows in {"rows": [...]} would break the dashboard."""
    body = client.get(PATH).json()
    assert isinstance(body, list)
    assert len(body) == 2


def test_rows_reach_the_client_with_columns_untouched(client):
    body = client.get(PATH).json()
    row = next(r for r in body if r["product_id"] == SEPTIME)
    assert set(row) == set(SEPTIME_ROW)
    assert row == SEPTIME_ROW


def test_empty_view_returns_an_empty_list(client, fake_supabase):
    fake_supabase.tables[("preorder", "vw_run_state")] = []
    assert client.get(PATH).json() == []


def test_null_data_still_serialises_as_an_array(monkeypatch):
    """A `null` body would make the dashboard's .map() throw.

    supabase-py returns a list today, so this pins the `or []` fallback against
    a client change rather than against current behaviour. FakeSupabase cannot
    produce this case, so the response is stubbed directly.
    """
    from main import app
    from routes import admin_preorders

    class NullData:
        def schema(self, _):
            return self

        def from_(self, _):
            return self

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def execute(self):
            class R:
                data = None

            return R()

    monkeypatch.setattr(admin_preorders, "supabase", NullData())
    app.dependency_overrides[admin_preorders.require_admin_token] = lambda: True
    try:
        assert TestClient(app).get(PATH).json() == []
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# The fields with teeth
# --------------------------------------------------------------------------

def test_current_run_reported_is_false_for_a_reissue_whose_prior_run_was_reported(client):
    """The whole point of the field.

    `already_reported` on /products is "ever reported", so a reissue inherits
    the prior run's badge. Someone reading that as current may queue the title
    for release, and fetch_banked_presales reads 0 for it.
    """
    row = next(r for r in client.get(PATH).json() if r["product_id"] == SEPTIME)
    assert row["prior_run_reported"] is True
    assert row["current_run_reported"] is False


def test_prior_run_columns_are_null_when_there_is_no_prior_run(client):
    """Null rather than 0, so a consumer cannot render them as real figures."""
    row = next(r for r in client.get(PATH).json() if r["product_id"] == CONTRA)
    assert row["has_prior_closed_run"] is False
    assert row["prior_run_presale_total"] is None
    assert row["prior_run_reported"] is None
    assert row["current_run_started_at"] is None


def test_inventory_is_not_clamped(client):
    """/products reports GREATEST(inventory, 0) and can never show oversell."""
    body = client.get(PATH).json()
    assert {r["product_id"]: r["inventory_unclamped"] for r in body} == {
        SEPTIME: -17,
        CONTRA: -88,
    }


def test_current_run_figures_are_per_line_and_per_unit_separately(client):
    """87 lines carrying 88 units: one order has two copies. Collapsing the two
    into one number would mis-state either the pick list or the customer count.
    """
    row = next(r for r in client.get(PATH).json() if r["product_id"] == CONTRA)
    assert row["current_run_open_lines"] == 87
    assert row["current_run_open_units"] == 88


# --------------------------------------------------------------------------
# Query and auth
# --------------------------------------------------------------------------

def test_product_id_filter_narrows_to_one_row(client):
    body = client.get(PATH, params={"product_id": SEPTIME}).json()
    assert [r["product_id"] for r in body] == [SEPTIME]


def test_reads_the_run_state_view_in_the_preorder_schema(client, fake_supabase):
    client.get(PATH)
    assert fake_supabase.calls_to("preorder", "vw_run_state")


def test_requires_the_admin_token(fake_supabase, monkeypatch):
    """No dependency override here: the real auth must reject."""
    from main import app
    from routes import admin_preorders

    monkeypatch.setattr(admin_preorders, "supabase", fake_supabase)
    monkeypatch.setattr(admin_preorders, "ADMIN_TOKEN", "sekret")
    app.dependency_overrides.clear()

    c = TestClient(app)
    assert c.get(PATH).status_code == 403
    assert c.get(PATH, headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert c.get(PATH, headers={"X-Admin-Token": "sekret"}).status_code == 200


def test_unset_admin_token_denies_rather_than_allows(fake_supabase, monkeypatch):
    """An empty expected token must not make the endpoint public."""
    from main import app
    from routes import admin_preorders

    monkeypatch.setattr(admin_preorders, "supabase", fake_supabase)
    monkeypatch.setattr(admin_preorders, "ADMIN_TOKEN", None)
    app.dependency_overrides.clear()

    c = TestClient(app)
    assert c.get(PATH, headers={"X-Admin-Token": ""}).status_code == 403
