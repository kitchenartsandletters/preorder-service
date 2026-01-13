"""
ANOMALY: MISSING COLLECTION (Rev 3)

Condition:
    - 'preorder' IN tags
    - AND in_preorder_collection == False
    - AND at least one FUTURE signal exists:
        • effective_pub_date > today
        • OR future date_tag
        • OR future pub_date
        • OR future override_date

This mismatch MUST produce:
    status="anomaly_missing_collection"

This anomaly overrides:
    - active_preorder
    - early_stock_arrival
"""

from datetime import date, timedelta

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


FUTURE_DATE = date.today() + timedelta(days=30)


def _assert_missing_collection(result):
    assert result.status == "anomaly_missing_collection"
    assert result.anomaly_type == "anomaly_missing_collection"


def test_missing_collection_future_effective_pub_date():
    """Tag present, collection=False, future effective_pub_date → anomaly_missing_collection."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        pub_date=FUTURE_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_missing_collection(result)


def test_missing_collection_future_date_tag_only():
    """Tag present, collection=False, future date_tag only → anomaly_missing_collection."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        date_tags=[FUTURE_DATE],
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_missing_collection(result)


def test_missing_collection_future_pub_date_only():
    """Tag present, collection=False, future pub_date only → anomaly_missing_collection."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        pub_date=FUTURE_DATE,
        inventory=5,
    )
    result = classify_preorder_product(product)
    _assert_missing_collection(result)


def test_missing_collection_future_override_date_only():
    """Tag present, collection=False, future override_date only → anomaly_missing_collection."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        override_date=FUTURE_DATE,
        inventory=-2,
    )
    result = classify_preorder_product(product)
    _assert_missing_collection(result)


def test_missing_collection_overrides_active_preorder():
    """
    Even when inventory <= 0 and future date would normally imply active_preorder,
    missing collection must take precedence.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        pub_date=FUTURE_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_missing_collection(result)


def test_missing_collection_overrides_early_stock_arrival():
    """
    Even when inventory > 0 and future date would normally imply early_stock_arrival,
    missing collection must take precedence.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        pub_date=FUTURE_DATE,
        inventory=10,
    )
    result = classify_preorder_product(product)
    _assert_missing_collection(result)