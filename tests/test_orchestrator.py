"""
Orchestrator tests for `classify_and_persist_product` / `batch_reclassify`,
run against the in-memory FakeSupabase (tests/fakes.py) so they assert
resulting DB state.

What each test pins (current behavior, verified against orchestrator.py):
- a classification is persisted to preorder.product_status;
- the DB override (preorder.product_overrides) WINS over the Shopify
  metafield override (`override_date_raw`) — the dual-source behavior that
  rev2 Move 1 must fold and then retire (see DOCS_STATUS E3);
- `has_inventory_arrival` is looked up from preorder.inventory_arrival and
  changes the result (delayed import vs stale collection);
- batch_reclassify continues past a product that genuinely raises.

The previous versions used MagicMock: the DB-override test stubbed a
`.single()` chain production never calls (it uses `.maybe_single()`), so no
override was ever injected; the batch test's "invalid" date tag is silently
dropped by `parsed_date_tags`, so nothing ever raised.
"""

from datetime import date, timedelta

from domain_models import ProductMetadata
from orchestrator import batch_reclassify, classify_and_persist_product

FUTURE = (date.today() + timedelta(days=60)).isoformat()
PAST = (date.today() - timedelta(days=30)).isoformat()


def make_product(**overrides) -> ProductMetadata:
    base = dict(
        product_id=1,
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags_raw=[],
        pub_date_raw=FUTURE,
        override_date_raw=None,
        inventory=0,
    )
    base.update(overrides)
    return ProductMetadata(**base)


def _status_row(sb, product_id):
    rows = [r for r in sb.rows("preorder", "product_status") if r["product_id"] == product_id]
    assert len(rows) == 1, rows
    return rows[0]


def test_classification_is_persisted(fake_supabase):
    result = classify_and_persist_product(
        supabase=fake_supabase, product_metadata=make_product(), engine_version="test-v"
    )

    assert result.status == "active_preorder"
    row = _status_row(fake_supabase, 1)
    assert row["status"] == "active_preorder"
    assert row["effective_pub_date"] == FUTURE
    assert row["engine_version"] == "test-v"


def test_db_override_wins_over_metafield_override(fake_supabase):
    fake_supabase.seed(
        "preorder", "product_overrides", [{"product_id": 1, "override_date_raw": "2099-06-01"}]
    )
    product = make_product(pub_date_raw=None, override_date_raw="2099-01-01")

    result = classify_and_persist_product(
        supabase=fake_supabase, product_metadata=product, engine_version="test-v"
    )

    assert result.effective_pub_date == date(2099, 6, 1)
    row = _status_row(fake_supabase, 1)
    assert row["effective_pub_date"] == "2099-06-01"
    # The DB value also replaces the metafield value in the persisted snapshot.
    assert row["metadata_snapshot"]["override_date"] == "2099-06-01"


def test_metafield_override_used_when_no_db_override(fake_supabase):
    product = make_product(pub_date_raw=None, override_date_raw="2099-01-01")

    result = classify_and_persist_product(
        supabase=fake_supabase, product_metadata=product, engine_version="test-v"
    )

    assert result.effective_pub_date == date(2099, 1, 1)


def test_inventory_arrival_lookup_changes_classification(fake_supabase):
    # Past pub date, still in collection, tagged, no stock.
    # No arrival record  -> delayed import -> active_preorder.
    product = make_product(pub_date_raw=PAST)
    result = classify_and_persist_product(
        supabase=fake_supabase, product_metadata=product, engine_version="test-v"
    )
    assert result.status == "active_preorder"

    # Arrival record present -> stock landed, still in collection -> stale.
    fake_supabase.seed("preorder", "inventory_arrival", [{"product_id": 1}])
    result = classify_and_persist_product(
        supabase=fake_supabase, product_metadata=make_product(pub_date_raw=PAST), engine_version="test-v"
    )
    assert result.status == "anomaly_stale_collection"


def test_batch_reclassify_continues_past_a_failing_product(fake_supabase):
    good = make_product(product_id=1)
    bad = make_product(product_id=2, pub_date_raw="not-a-date")  # fromisoformat raises

    results = batch_reclassify(
        supabase=fake_supabase, products=[good, bad], engine_version="test-v"
    )

    assert len(results) == 1
    persisted = {r["product_id"] for r in fake_supabase.rows("preorder", "product_status")}
    assert persisted == {1}
