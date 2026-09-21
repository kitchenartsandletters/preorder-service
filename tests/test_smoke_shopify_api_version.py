"""
Offline tests for scripts/smoke_shopify_api_version.py.

The script can only be run against live Shopify with production credentials,
so its extraction and decision logic are tested here with a stubbed transport:

- it extracts exactly the production read queries that have variable
  factories (a new production query fails this test until smoke coverage is
  added), and never a mutation;
- every factory supplies every `$variable` its query declares;
- verdict: REGRESSION blocks, PRE-EXISTING does not, and a target the server
  did not actually serve blocks.
"""

import importlib.util
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "smoke_shopify_api_version", REPO / "scripts" / "smoke_shopify_api_version.py"
)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

CTX = {
    "product_id": 1,
    "product_gid": "gid://shopify/Product/1",
    "inventory_item_gid": "gid://shopify/InventoryItem/2",
    "profile_gid": "gid://shopify/DeliveryProfile/3",
    "reference_profile_gid": "gid://shopify/DeliveryProfile/4",
}


def test_extracts_exactly_the_covered_read_queries():
    ops = smoke.extract_operations()
    assert set(ops) == set(smoke.VARIABLE_FACTORIES)
    assert len(ops) == 15


def test_every_query_bearing_file_is_covered_or_explicitly_excluded():
    """A new file that sends GraphQL must be added to SOURCE_FILES (and get
    variable factories) or to EXCLUDED_SOURCE_FILES with a reason."""
    import ast

    op = re.compile(r"^\s*(query|mutation)\s+\w+\s*[({]")
    skip_dirs = {"tests", "scripts", ".git", "node_modules", "__pycache__", ".venv", "venv"}
    bearing = set()
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO)
        if skip_dirs.intersection(rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and op.match(n.value)
               for n in ast.walk(tree)):
            bearing.add(rel.as_posix())
    accounted = set(smoke.SOURCE_FILES) | set(smoke.EXCLUDED_SOURCE_FILES)
    assert bearing - accounted == set(), f"not smoke-covered or excluded: {sorted(bearing - accounted)}"
    assert all(smoke.EXCLUDED_SOURCE_FILES.values()), "every exclusion needs a reason"


def test_never_extracts_mutations():
    for text in smoke.extract_operations().values():
        assert text.lstrip().startswith("query")
        assert not re.search(r"\bmutation\b", text)


@pytest.mark.parametrize("key", sorted(smoke.VARIABLE_FACTORIES))
def test_factory_supplies_every_declared_variable(key):
    text = smoke.extract_operations()[key]
    header = text[: text.index("{")]
    declared = set(re.findall(r"\$(\w+)\s*:", header))
    supplied = set(smoke.VARIABLE_FACTORIES[key](CTX))
    assert declared == supplied, f"{key}: declared {declared}, supplied {supplied}"


# ---- verdict logic with a stubbed transport ---------------------------------

class _Resp:
    def __init__(self, served, ok=True):
        self.status_code = 200
        self.headers = {"X-Shopify-API-Version": served}
        self._ok = ok

    def json(self):
        return {"data": {"x": 1}} if self._ok else {"errors": [{"message": "boom"}], "data": None}


SETUP_BODY = {
    "data": {
        "product": {"id": "gid://shopify/Product/1",
                    "variants": {"nodes": [{"inventoryItem": {"id": "gid://shopify/InventoryItem/2"}}]}},
        "deliveryProfiles": {"nodes": [{"id": "gid://shopify/DeliveryProfile/3", "default": True}]},
    }
}


def _run(monkeypatch, behave, mds=False):
    """behave(version, op_name) -> (served_version, ok)."""
    import shopify_token

    monkeypatch.setenv("SHOP_URL", "test-shop.myshopify.com")
    monkeypatch.setattr(shopify_token, "get_token_sync", lambda *a, **k: "tok")

    def fake_post(client, domain, token, version, query, variables):
        if "SmokeSetup" in query:
            return _Resp(version), SETUP_BODY
        if "SmokeMarketDrivenShipping" in query:
            return _Resp(version), {"data": {"shop": {"features": {"marketDrivenShipping": mds}}}}
        name = re.match(r"\s*query\s+(\w+)", query).group(1)
        served, ok = behave(version, name)
        r = _Resp(served, ok)
        return r, r.json()

    monkeypatch.setattr(smoke, "_post", fake_post)
    return smoke.main(["--baseline", "2026-01", "--target", "2026-04"])


def test_all_ok_is_safe(monkeypatch, capsys):
    assert _run(monkeypatch, lambda v, n: (v, True)) == 0
    assert "SAFE TO BUMP" in capsys.readouterr().out


def test_regression_blocks(monkeypatch, capsys):
    code = _run(monkeypatch, lambda v, n: (v, not (v == "2026-04" and n == "ProductFull")))
    out = capsys.readouterr().out
    assert code == 1
    assert "REGRESSIONS (block the bump): 1" in out and "shopify_service.py::ProductFull" in out


def test_preexisting_error_does_not_block(monkeypatch, capsys):
    code = _run(monkeypatch, lambda v, n: (v, n != "WeekSales"))
    out = capsys.readouterr().out
    assert code == 0
    assert "PRE-EXISTING errors (fail on both; not caused by the bump): 1" in out


def test_market_driven_shipping_is_reported_but_never_blocks(monkeypatch, capsys):
    code = _run(monkeypatch, lambda v, n: (v, True), mds=True)
    out = capsys.readouterr().out
    assert code == 0
    assert "marketDrivenShipping=True" in out and "WARNING: shop is on market-driven shipping" in out


def test_target_not_actually_served_blocks(monkeypatch, capsys):
    code = _run(monkeypatch, lambda v, n: ("2026-01", True))
    out = capsys.readouterr().out
    assert code == 1
    assert "requested 2026-04, served 2026-01" in out
