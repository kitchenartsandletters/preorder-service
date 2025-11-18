"""
ACTIVE PREORDER TESTS (Rev 3)

A product MUST classify as active_preorder when ALL of the following are true:

    1. At least one future-dated PREORDER signal:
        - effective_pub_date > today
        - OR a future date_tag when no metafields exist
        - OR in_preorder_collection == True
        - OR 'preorder' in tags AND not all dates are past

    2. inventory <= 0
        - inventory == 0 (normal preorder)
        - inventory < 0 (oversold preorder)

    3. No anomaly_* conditions match
    4. Not in the early_stock_arrival condition (which requires inventory > 0)

TODO CASES TO IMPLEMENT:

1. Future effective_pub_date & inventory = 0 → active_preorder
2. Future effective_pub_date & inventory < 0 → active_preorder
3. Future date_tag only (no metafields) & inventory <= 0 → active_preorder
4. In preorder collection alone (no future dates) & inventory <= 0 → active_preorder
5. Has preorder tag alone (no future dates) & inventory <= 0 → active_preorder
6. Active preorder blocked when an anomaly_* condition applies
"""