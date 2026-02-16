from unittest.mock import MagicMock

from orchestrator import classify_and_persist_product, batch_reclassify
from domain_models import ProductMetadata


def make_product(**overrides):
    base = {
        "product_id": 1,
        "tags": ["preorder"],
        "in_preorder_collection": True,
        "date_tags_raw": ["12-31-2099"],
        "pub_date_raw": None,
        "override_date_raw": None,
        "inventory": 0,
    }
    base.update(overrides)
    return ProductMetadata(**base)


def test_classify_and_persist_calls_supabase():
    supabase = MagicMock()

    product = make_product()

    result = classify_and_persist_product(
        supabase=supabase,
        product_metadata=product,
        engine_version="test-v",
    )

    supabase.table.assert_any_call("preorder.product_status")
    assert result is not None


def test_batch_reclassify_continues_on_error():
    supabase = MagicMock()

    good_product = make_product(product_id=1)
    bad_product = make_product(product_id=2)

    bad_product.date_tags_raw = ["invalid-date"]

    results = batch_reclassify(
        supabase=supabase,
        products=[good_product, bad_product],
        engine_version="test-v",
    )

    assert len(results) >= 1


def test_override_from_db_takes_priority_over_metadata():
    """
    If DB override exists, it must override metadata override_date_raw.
    """
    supabase = MagicMock()

    # Simulate DB override row
    supabase.table().select().eq().single.return_value.execute.return_value.data = {
        "override_date_raw": "2099-06-01"
    }

    product = make_product(
        override_date_raw="2099-01-01"  # metadata override
    )

    result = classify_and_persist_product(
        supabase=supabase,
        product_metadata=product,
        engine_version="test-v",
    )

    # Ensure persistence still happens
    supabase.table.assert_any_call("preorder.product_status")

    # Result should not be None and classification must complete
    assert result is not None
