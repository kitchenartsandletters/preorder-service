"""
test_anomaly_inventory_contradiction.py

Anomaly 4.6 — Future pub date but positive inventory (not yet marked as early arrival).

TODO:
- Future pub_date + inventory > 0 → anomaly_inventory_contradiction
- Future override_date + inventory > 0 → anomaly
- Future date_tag + inventory > 0 → anomaly
- Validate against exceptions (later phase)
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input