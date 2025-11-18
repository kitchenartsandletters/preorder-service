"""
ANOMALY: MULTI-DATE CONFLICT (Rev 3)

Triggered ONLY when ALL are true:

- len(date_tags) >= 2
- pub_date is None
- override_date is None

I.e., multiple historical pub dates exist but there is no canonical value.

TODO CASES TO IMPLEMENT:

1. Multiple date_tags, no pub_date, no override_date → anomaly_multi_date_conflict
2. Ensure NOT triggered when pub_date matches latest_tag
3. Ensure NOT triggered when override_date exists
4. Ensure date_tag order in the tag array does not matter (sorting works)
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


def test_multiple_tags_no_pub_or_override():
    """len(date_tags) >= 2 AND no pub_date AND no override_date → anomaly_multi_date_conflict."""
    pass


def test_not_triggered_when_pub_date_matches_latest_tag():
    """If pub_date exists and equals latest_tag, do NOT classify as anomaly_multi_date_conflict."""
    pass


def test_not_triggered_when_override_exists():
    """If override_date exists, do NOT classify as anomaly_multi_date_conflict."""
    pass


def test_date_tag_order_does_not_matter():
    """Tag order should not affect conflict detection (sorting must be applied)."""
    pass