"""
test_active_preorder.py

Tests detection of ACTIVE PREORDER state.

TODO:
- One future signal AND no anomalies
- Future date_tag
- Future pub_date
- Future override_date
- Membership in preorder collection
- Permanent preorder tag
- Edge cases where anomalies block active status
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input