"""
ANOMALY: OVERRIDE CONFLICT (Rev 3)

Triggered when override_date contradicts known history:

- override_date < pub_date
- OR override_date < latest date_tag
- OR override_date is chronologically older than any known official date

TODO CASES TO IMPLEMENT:

1. override_date < pub_date → anomaly_override_conflict
2. override_date < latest_tag → anomaly_override_conflict
3. override_date < today while pub_date > today → anomaly_override_conflict
4. Situations that should instead be pubdate_conflict (negative tests)
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


def test_override_date_earlier_than_pub_date():
    """override_date < pub_date → anomaly_override_conflict."""
    pass


def test_override_date_earlier_than_latest_tag():
    """override_date < latest_tag → anomaly_override_conflict."""
    pass


def test_override_date_earlier_than_today_while_pubdate_future():
    """override_date < today AND pub_date > today → anomaly_override_conflict."""
    pass


def test_conflict_should_be_pubdate_conflict_not_override_conflict():
    """
    Negative test:
    When pub_date mismatches date_tags but override_date is valid,
    classification should yield anomaly_pubdate_conflict instead.
    """
    pass