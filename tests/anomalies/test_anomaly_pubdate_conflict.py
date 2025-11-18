"""
ANOMALY: PUBDATE CONFLICT (Rev 3)

Triggered when date_tag history and metafields disagree in invalid ways.

Main patterns:

Case A — Single date_tag, no override:
    - one date_tag
    - no override_date
    - pub_date != date_tag → anomaly_pubdate_conflict

Case B — Multiple tags, no override:
    - multiple date_tags
    - no override_date
    - pub_date exists AND pub_date != latest_tag → anomaly_pubdate_conflict

Case C — Conflicts between tags, pub_date, override_date:
    - effective sources disagree in ways not covered by anomaly_override_conflict

TODO CASES TO IMPLEMENT:

1. One tag, pub_date mismatch → anomaly_pubdate_conflict
2. Multiple tags, pub_date not equal to latest → anomaly_pubdate_conflict
3. Tags present, pub_date present, but neither matches latest_tag → anomaly_pubdate_conflict
4. Mixed conditions where conflict should be resolved as override_conflict instead (negative test)
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


def test_single_tag_no_override_pubdate_mismatch():
    """Case A: one date_tag, no override_date, pub_date != date_tag → anomaly_pubdate_conflict."""
    pass


def test_multiple_tags_no_override_pubdate_not_latest():
    """Case B: multiple date_tags, no override_date, pub_date != latest_tag → anomaly_pubdate_conflict."""
    pass


def test_pubdate_and_tags_disagree_without_override():
    """Case C: pub_date present, tags present, neither matches latest_tag → anomaly_pubdate_conflict."""
    pass


def test_conflict_should_be_override_conflict_not_pubdate_conflict():
    """Negative test: override_date < pub_date should produce anomaly_override_conflict, not pubdate_conflict."""
    pass