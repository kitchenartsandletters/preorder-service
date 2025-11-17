"""
test_anomaly_override_conflict.py

Anomaly 4.4 — Override date contradicts real pub date ordering.

TODO:
- override_date earlier than pub_date → anomaly
- override_date contradicting date_tags → anomaly
- Ensure override precedence still applies after anomaly resolution
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input