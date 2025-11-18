"""
NOT A PREORDER PRODUCT TESTS (Rev 3)

Returned when:
    - no anomalies
    - not early_stock_arrival
    - not active_preorder
    - not historical_preorder

These are normal, non-preorder catalog items.

TODO CASES TO IMPLEMENT:

1. No tags, no collection, no dates → not_a_preorder_product
2. Tagless product with past dates only → not_a_preorder_product
3. Product with no preorder tag but positive inventory and past pub_date → not_a_preorder_product
4. Product with no preorder signals but in a random collection → not_a_preorder_product
5. Ensure this is the lowest-priority fallback
"""
from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


def test_no_tags_no_collection_no_dates():
    """No tags, no collection, no dates → not_a_preorder_product."""
    pass


def test_tagless_with_past_dates_only():
    """Tagless product with past dates only → not_a_preorder_product."""
    pass


def test_no_preorder_tag_positive_inventory_past_pubdate():
    """No preorder tag + past pub_date + inventory > 0 → not_a_preorder_product."""
    pass


def test_no_preorder_signals_random_collection():
    """Product with no preorder signals but in a random collection → not_a_preorder_product."""
    pass


def test_lowest_priority_fallback():
    """Ensure this classification is returned only when all other categories are excluded."""
    pass