"""
ANOMALY: MISSING TAG (Rev 3)

Condition:
    - in_preorder_collection == True
    - AND 'preorder' NOT in tags

This mismatch MUST produce:
    status="anomaly_missing_tag"

Inventory and date signals are irrelevant and must NOT downgrade the anomaly.
"""

from datetime import date

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


def _assert_missing_tag(result):
    assert result.status == "anomaly_missing_tag"
    assert result.anomaly_type == "anomaly_missing_tag"


def test_missing_tag_in_preorder_collection():
    """In preorder collection + missing 'preorder' tag → anomaly_missing_tag."""
    product = make_input(
        in_preorder_collection=True,
        tags=[],
    )
    result = classify_preorder_product(product)
    _assert_missing_tag(result)


def test_missing_tag_inventory_positive():
    """Inventory > 0 still triggers anomaly_missing_tag."""
    product = make_input(
        in_preorder_collection=True,
        tags=[],
        inventory=5,
    )
    result = classify_preorder_product(product)
    _assert_missing_tag(result)


def test_missing_tag_inventory_zero():
    """Inventory == 0 still triggers anomaly_missing_tag."""
    product = make_input(
        in_preorder_collection=True,
        tags=[],
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_missing_tag(result)


def test_missing_tag_inventory_negative():
    """Inventory < 0 still triggers anomaly_missing_tag."""
    product = make_input(
        in_preorder_collection=True,
        tags=[],
        inventory=-3,
    )
    result = classify_preorder_product(product)
    _assert_missing_tag(result)


def test_missing_tag_future_dates_do_not_override():
    """Future dates cannot downgrade anomaly_missing_tag to active_preorder."""
    product = make_input(
        in_preorder_collection=True,
        tags=[],
        date_tags=[date(2026, 1, 15)],
        pub_date=date(2026, 1, 15),
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_missing_tag(result)


def test_missing_tag_past_dates_do_not_override():
    """Past dates cannot downgrade anomaly_missing_tag to historical_preorder."""
    product = make_input(
        in_preorder_collection=True,
        tags=[],
        date_tags=[date(2024, 1, 1)],
        pub_date=date(2024, 1, 1),
        inventory=10,
    )
    result = classify_preorder_product(product)
    _assert_missing_tag(result)


def test_missing_tag_never_not_a_preorder_product():
    """This mismatch must never classify as not_a_preorder_product."""
    product = make_input(
        in_preorder_collection=True,
        tags=[],
    )
    result = classify_preorder_product(product)
    assert result.status != "not_a_preorder_product"
    _assert_missing_tag(result)