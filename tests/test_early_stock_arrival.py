"""
EARLY STOCK ARRIVAL TESTS (Rev 3)

A product MUST classify as early_stock_arrival when ALL of the following are true:

    1. effective_pub_date > today
         - derived via override_date
         - OR pub_date
         - OR latest date_tag when no metafields exist

    2. inventory > 0

    3. No anomaly_* conditions match:
         - NOT missing_tag
         - NOT missing_collection
         - NOT pubdate_conflict
         - NOT override_conflict
         - NOT multi_date_conflict

If these conditions are met, the classifier MUST return:
    status="early_stock_arrival"
    anomaly_type=None

This is NOT an anomaly_* status.
It MUST be tested separately from anomaly cases.

TODO CASES TO IMPLEMENT:

1. Future effective_pub_date (override) & inventory > 0 → early_stock_arrival
2. Future effective_pub_date (pub_date) & inventory > 0 → early_stock_arrival
3. Future date_tag only (no metafields) & inventory > 0 → early_stock_arrival
4. Product in preorder collection & future effective date & inventory > 0 → early_stock_arrival
5. Product with preorder tag (no collection) & future effective date & inventory > 0 → early_stock_arrival
6. Ensure early_stock_arrival does NOT classify as active_preorder
      (inventory > 0 prevents active_preorder)
7. Ensure early_stock_arrival does NOT classify as historical_preorder
      (future date prevents historical)
8. Ensure anomalies override early_stock_arrival:
      - if any anomaly_* condition is met → anomaly_* takes priority
      (negative tests)
9. Ensure not_a_preorder_product is NOT returned for these conditions

Imports needed when implementing tests:
    from classification.engine import classify_preorder_product
    from tests.fixtures_product_inputs import make_input

"""
from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


def test_future_override_inventory_positive():
    """Future effective_pub_date via override_date & inventory > 0 → early_stock_arrival."""
    pass


def test_future_pubdate_inventory_positive():
    """Future effective_pub_date via pub_date & inventory > 0 → early_stock_arrival."""
    pass


def test_future_date_tag_only_inventory_positive():
    """Future latest date_tag (no metafields) & inventory > 0 → early_stock_arrival."""
    pass


def test_in_preorder_collection_future_date_inventory_positive():
    """In preorder collection + future effective date + inventory > 0 → early_stock_arrival."""
    pass


def test_preorder_tag_only_future_effective_date_inventory_positive():
    """Has preorder tag, future effective date, inventory > 0 → early_stock_arrival."""
    pass


def test_inventory_positive_blocks_active_preorder():
    """inventory > 0 MUST NOT classify as active_preorder even with future date."""
    pass


def test_future_date_blocks_historical_preorder():
    """Future effective date MUST NOT classify as historical_preorder."""
    pass


def test_anomalies_override_early_stock_arrival():
    """Any anomaly_* condition must override early_stock_arrival (negative tests)."""
    pass


def test_not_not_a_preorder_product_for_valid_early_stock():
    """A valid early_stock_arrival scenario MUST NOT classify as not_a_preorder_product."""
    pass