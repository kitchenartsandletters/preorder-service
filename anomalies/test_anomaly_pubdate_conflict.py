"""
test_anomaly_pubdate_conflict.py

Anomaly 4.3 — Pubdate conflict between:
- date_tags
- primary pub_date metafield
- override_date metafield

TODO:
- Mismatched date_tag vs pub_date → anomaly
- Mismatched pub_date vs override_date → anomaly
- Earliest date_tags conflict → anomaly
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input