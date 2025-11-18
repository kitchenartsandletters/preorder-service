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