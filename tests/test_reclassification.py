from unittest.mock import MagicMock
from datetime import date

from reclassification import reclassify_single_product, reclassify_batch
from domain_models import ProductMetadata


def make_product(product_id=1):
    return ProductMetadata(
        product_id=product_id,
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags_raw=["2099-01-01"],
        pub_date_raw=None,
        override_date_raw=None,
        inventory=0,
    )


def test_reclassify_single_returns_summary():
    supabase = MagicMock()
    product = make_product()

    result = reclassify_single_product(
        supabase=supabase,
        product_metadata=product,
        engine_version="v-test",
    )

    assert result["product_id"] == product.product_id
    assert result["engine_version"] == "v-test"
    assert "reclassified_at" in result


def test_reclassify_batch_summary_counts():
    supabase = MagicMock()

    products = [
        make_product(1),
        make_product(2),
    ]

    result = reclassify_batch(
        supabase=supabase,
        products=products,
        engine_version="v-test",
    )

    assert result["total"] == 2
    assert result["success_count"] == 2
    assert result["failure_count"] == 0


def test_batch_continues_on_exception():
    supabase = MagicMock()

    good = make_product(1)
    bad = make_product(2)

    # Inject invalid structure to force failure
    bad.date_tags_raw = None

    result = reclassify_batch(
        supabase=supabase,
        products=[good, bad],
        engine_version="v-test",
    )

    assert result["total"] == 2
    assert result["success_count"] >= 1
    assert result["failure_count"] >= 1