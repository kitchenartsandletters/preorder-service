"""
ANOMALY: MISSING TAG (Rev 3)

Condition:
    - in_preorder_collection == True
    - AND 'preorder' NOT in tags

This mismatch MUST produce:
    status="anomaly_missing_tag"

TODO CASES TO IMPLEMENT:

1. In preorder collection + missing tag → anomaly_missing_tag
2. Inventory irrelevant (test >0, 0, <0)
3. Future/past dates irrelevant (still anomaly)
4. Cannot downgrade to active/historical/not_a_preorder_product
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


def test_missing_tag_in_preorder_collection():
    """In preorder collection + missing 'preorder' tag → anomaly_missing_tag."""
    pass


def test_missing_tag_inventory_positive():
    """Inventory > 0 still triggers anomaly_missing_tag."""
    pass


def test_missing_tag_inventory_zero():
    """Inventory == 0 still triggers anomaly_missing_tag."""
    pass


def test_missing_tag_inventory_negative():
    """Inventory < 0 still triggers anomaly_missing_tag."""
    pass


def test_missing_tag_future_dates_do_not_override():
    """Future dates cannot downgrade anomaly_missing_tag to active_preorder."""
    pass


def test_missing_tag_past_dates_do_not_override():
    """Past dates cannot downgrade anomaly_missing_tag to historical_preorder."""
    pass


def test_missing_tag_never_not_a_preorder_product():
    """This mismatch must never classify as not_a_preorder_product."""
    pass