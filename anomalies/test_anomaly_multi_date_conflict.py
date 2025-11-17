"""
test_anomaly_multi_date_conflict.py

Anomaly 4.5 — Multiple date tags in impossible order.

TODO:
- Two date_tags where later tag < earlier tag → anomaly_multi_date_conflict
- Confirm conflict detection independent of other fields
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input