"""
ANOMALY: PUBDATE CONFLICT (Rev 3)

Triggered when date_tag history and metafields disagree in invalid ways,
*excluding* cases handled by anomaly_override_conflict.

Cases covered here assume:
- override_date is None OR override_date is valid (non-conflicting)

Main patterns:

Case A — Single date_tag, no override:
    - one date_tag
    - no override_date
    - pub_date != date_tag

Case B — Multiple tags, no override:
    - multiple date_tags
    - no override_date
    - pub_date exists AND pub_date != latest_tag

Case C — Tags present, pub_date present:
    - pub_date does not equal latest_tag
    - override_date is None
"""

from datetime import date, timedelta

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


TODAY = date.today()
PAST_DATE = TODAY - timedelta(days=60)
MID_DATE = TODAY - timedelta(days=10)
FUTURE_DATE = TODAY + timedelta(days=30)


def _assert_pubdate_conflict(result):
    assert result.status == "anomaly_pubdate_conflict"
    assert result.anomaly_type == "anomaly_pubdate_conflict"


def test_single_tag_no_override_pubdate_mismatch():
    """Case A: one date_tag, no override_date, pub_date != date_tag."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags=[FUTURE_DATE],
        pub_date=MID_DATE,
        override_date=None,
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_pubdate_conflict(result)


def test_multiple_tags_no_override_pubdate_not_latest():
    """Case B: multiple date_tags, no override_date, pub_date != latest_tag."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags=[PAST_DATE, FUTURE_DATE],
        pub_date=PAST_DATE,
        override_date=None,
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_pubdate_conflict(result)


def test_pubdate_and_tags_disagree_without_override():
    """
    Case C: pub_date present, multiple tags present,
    pub_date does not equal latest_tag, override_date absent.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags=[PAST_DATE, MID_DATE, FUTURE_DATE],
        pub_date=MID_DATE,
        override_date=None,
        inventory=5,
    )
    result = classify_preorder_product(product)
    _assert_pubdate_conflict(result)


def test_conflict_should_be_override_conflict_not_pubdate_conflict():
    """
    Negative test:
    When override_date exists and is earlier than pub_date,
    anomaly_override_conflict must take precedence.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags=[FUTURE_DATE],
        pub_date=FUTURE_DATE,
        override_date=PAST_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)

    assert result.status == "anomaly_override_conflict"
    assert result.anomaly_type == "anomaly_override_conflict"