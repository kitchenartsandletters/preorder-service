"""
Tests for override_service.update_override_date_and_reclassify.

SKIPPED DELIBERATELY — see docs/DOCS_STATUS.md Landmine 6.

The function under test is dead code: no route calls it. It is also broken
against the real schema: it upserts an `updated_by` column that
preorder.product_overrides does not have. A mock cannot detect that, so making
this test pass would report a known-broken path as healthy.

The whole override path (this function, `fetch_override_date`, the
product_overrides table, and this test file) is deleted in rev2 Move 1g
(docs/phase_unified_pubdate_rev2.md). The read side (`fetch_override_date`,
DB override wins over the metafield) is covered by
tests/test_orchestrator.py until then.
"""

import pytest
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


@pytest.mark.skip(
    reason="Landmine 6: dead code (no route) that writes a column product_overrides "
    "lacks; deleted in rev2 Move 1g. Not green-washed with a mock."
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
