"""
test_historical_preorder.py

Tests detection of HISTORICAL PREORDER state.

TODO:
- preorder tag present
- all date signals are in the past
- NOT in preorder collection
- normal inventory
- must block if any anomaly exists
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input