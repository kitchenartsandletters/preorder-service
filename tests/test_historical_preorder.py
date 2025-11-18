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