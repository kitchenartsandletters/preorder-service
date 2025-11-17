"""
test_anomaly_missing_collection.py

Anomaly 4.2 — Product has preorder tag BUT is not in preorder collection 
AND has a future pub/on-sale date.

TODO:
- Tagged preorder + future date + NOT in collection → anomaly_missing_collection
- Validate future signals from:
    - date_tag
    - pub_date
    - override_date
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input