from unittest.mock import MagicMock
from datetime import date

from domain_models import ProductMetadata
from override_service import update_override_date_and_reclassify


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


def test_override_date_update_triggers_reclassification():
    supabase = MagicMock()
    # Simulate no existing DB override row
    supabase.table().select().eq().single.return_value.execute.return_value.data = {}

    product = make_product()

    result = update_override_date_and_reclassify(
        supabase=supabase,
        product_metadata=product,
        new_override_date_raw="2099-05-01",
        engine_version="test-v",
    )

    # Supabase update should have been called
    supabase.table.assert_any_call("preorder.product_overrides")

    # Local object updated
    assert product.override_date_raw == "2099-05-01"

    # Should return structured summary
    assert result["product_id"] == product.product_id
    assert result["engine_version"] == "test-v"