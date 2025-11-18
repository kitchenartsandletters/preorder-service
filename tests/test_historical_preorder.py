"""
HISTORICAL PREORDER TESTS (Rev 3)

A product MUST classify as historical_preorder when ALL are true:

    1. 'preorder' tag IS present (permanent historical identity)
    2. All dates are in the past:
         - effective_pub_date <= today
         - OR (max(date_tags) <= today when no metafields)
    3. NOT in the preorder collection
    4. No anomaly_* conditions
    5. Not early_stock_arrival
    6. Inventory ANY VALUE is allowed (>0, =0, <0)

TODO CASES TO IMPLEMENT:

1. Tag present, past effective_pub_date, not in collection → historical_preorder
2. Tag present, no metafields, max(date_tags) past → historical_preorder
3. Tag present, no date info at all → historical_preorder
4. Inventory > 0 still yields historical_preorder
5. Inventory == 0 still yields historical_preorder
6. Inventory < 0 still yields historical_preorder
7. Historical_preorder blocked when an anomaly_* condition applies
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


def test_tag_present_past_effective_pub_date_not_in_collection():
    """Tag present, past effective_pub_date, not in collection → historical_preorder."""
    pass


def test_tag_present_no_metafields_latest_date_tag_past():
    """Tag present, no metafields, max(date_tags) past → historical_preorder."""
    pass


def test_tag_present_no_date_info_at_all():
    """Tag present, no date_tags, no pub_date, no override_date → historical_preorder."""
    pass


def test_inventory_positive_still_historical():
    """Inventory > 0 still yields historical_preorder."""
    pass


def test_inventory_zero_still_historical():
    """Inventory == 0 still yields historical_preorder."""
    pass


def test_inventory_negative_still_historical():
    """Inventory < 0 still yields historical_preorder."""
    pass


def test_anomaly_blocks_historical_preorder():
    """Historical_preorder is blocked when an anomaly_* condition applies."""
    pass