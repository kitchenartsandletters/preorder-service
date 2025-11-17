"""
test_anomaly_missing_tag.py

Anomaly 4.1 — Product is in preorder collection BUT missing preorder tag.

TODO:
- Product in preorder collection + missing tag → anomaly_missing_tag
- Inventory should not matter here
- Ensure this anomaly overrides all other states
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input