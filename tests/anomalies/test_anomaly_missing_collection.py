"""
ANOMALY: MISSING COLLECTION (Rev 3)

Condition:
    - 'preorder' in tags
    - AND NOT in preorder collection
    - AND at least one future signal:
         - effective_pub_date > today
         - OR future date_tag
         - OR future pub_date
         - OR future override_date

TODO CASES TO IMPLEMENT:

1. Tag present, collection=False, future effective_pub_date → anomaly_missing_collection
2. Tag present, collection=False, future date_tag only → anomaly_missing_collection
3. Tag present, collection=False, future pub_date only → anomaly_missing_collection
4. Tag present, collection=False, future override only → anomaly_missing_collection
5. Should override active_preorder classification
6. Should override early_stock_arrival classification
"""