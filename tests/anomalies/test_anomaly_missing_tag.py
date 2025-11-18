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