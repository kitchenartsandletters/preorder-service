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
"""

from datetime import date, timedelta

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


TODAY = date.today()
PAST_DATE = TODAY - timedelta(days=30)


def _assert_historical(result):
    assert result.status == "historical_preorder"
    assert result.anomaly_type is None


def test_tag_present_past_effective_pub_date_not_in_collection():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        pub_date=PAST_DATE,
        inventory=5,
    )
    result = classify_preorder_product(product)
    _assert_historical(result)


def test_tag_present_no_metafields_latest_date_tag_past():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        date_tags=[PAST_DATE],
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_historical(result)


def test_tag_present_no_date_info_at_all():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        date_tags=[],
        pub_date=None,
        override_date=None,
        inventory=10,
    )
    result = classify_preorder_product(product)
    _assert_historical(result)


def test_inventory_positive_still_historical():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        pub_date=PAST_DATE,
        inventory=10,
    )
    result = classify_preorder_product(product)
    _assert_historical(result)


def test_inventory_zero_still_historical():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        pub_date=PAST_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_historical(result)


def test_inventory_negative_still_historical():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        pub_date=PAST_DATE,
        inventory=-5,
    )
    result = classify_preorder_product(product)
    _assert_historical(result)


def test_anomaly_blocks_historical_preorder():
    """
    If an anomaly condition applies (e.g. missing collection with future date),
    historical_preorder must NOT trigger.
    """
    future_date = TODAY + timedelta(days=30)

    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        pub_date=future_date,  # triggers anomaly_missing_collection
        inventory=5,
    )

    result = classify_preorder_product(product)

    assert result.status.startswith("anomaly_")