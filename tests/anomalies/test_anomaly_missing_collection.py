"""
ANOMALY: MISSING COLLECTION (Rev 3)

Condition:
    - 'preorder' in tags
    - AND NOT in preorder collection
    - AND at least one future signal:
         - effective_pub_date > today
         - OR future date_tag
         - OR future pub_date
         - OR future override_date

TODO CASES TO IMPLEMENT:

1. Tag present, collection=False, future effective_pub_date → anomaly_missing_collection
2. Tag present, collection=False, future date_tag only → anomaly_missing_collection
3. Tag present, collection=False, future pub_date only → anomaly_missing_collection
4. Tag present, collection=False, future override only → anomaly_missing_collection
5. Should override active_preorder classification
6. Should override early_stock_arrival classification
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


def test_missing_collection_future_effective_pub_date():
    """Tag present, collection=False, future effective_pub_date → anomaly_missing_collection."""
    pass


def test_missing_collection_future_date_tag_only():
    """Tag present, collection=False, future date_tag only → anomaly_missing_collection."""
    pass


def test_missing_collection_future_pub_date_only():
    """Tag present, collection=False, future pub_date only → anomaly_missing_collection."""
    pass


def test_missing_collection_future_override_date_only():
    """Tag present, collection=False, future override_date only → anomaly_missing_collection."""
    pass


def test_missing_collection_overrides_active_preorder():
    """Should override active_preorder classification when future signals exist."""
    pass


def test_missing_collection_overrides_early_stock_arrival():
    """Should override early_stock_arrival classification when future signals exist."""
    pass