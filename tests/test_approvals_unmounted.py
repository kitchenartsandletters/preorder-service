"""
Guard: the /approvals router stays unmounted (docs/DOCS_STATUS.md Landmine 9).

routes/approvals.py was included in main.py with no auth dependency. The only
app-wide middleware is CORS, which restricts browsers but not scripts, so
`GET /approvals/list` was world-readable and `POST /approvals/approve` and
`/approvals/revoke` were world-writable — the latter two writing to
preorder.approvals, including its override_pub_date column.

Nothing calls it: no caller in this repo or in admin-dashboard, and the table
is dormant. Owner decision 2026-09-22 was to unmount rather than add auth.

The router file is kept, so if it is ever mounted again this test fails and
whoever does it has to add authentication and update the landmine entry.
"""

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

APPROVALS_PATHS = [
    ("get", "/approvals/list"),
    ("post", "/approvals/approve"),
    ("post", "/approvals/revoke"),
]


@pytest.mark.parametrize("method,path", APPROVALS_PATHS)
def test_approvals_endpoints_are_not_routable(method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 404, (
        f"{method.upper()} {path} is reachable again. If that is intentional, it "
        "needs an auth dependency first (Landmine 9)."
    )


# NOTE: this FastAPI version keeps included routers as _IncludedRouter objects
# rather than flattening them into app.routes, so app.routes carries no usable
# paths. The OpenAPI schema is the reliable enumeration.

def test_no_approvals_route_is_registered():
    paths = app.openapi()["paths"]
    assert [p for p in paths if "approval" in p] == []


def test_the_rest_of_the_api_is_still_mounted():
    """The unmount must not take anything else with it."""
    paths = set(app.openapi()["paths"])
    assert "/healthz" in paths
    assert any(p.startswith("/webhooks") for p in paths)
    assert any(p.startswith("/reclassify") for p in paths)
    assert any(p.startswith("/admin/preorders") for p in paths)
    assert "/admin/preorders/holds" in paths
    assert len(paths) > 40  # the whole API, not a stub
