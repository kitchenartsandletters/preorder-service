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