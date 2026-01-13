"""
ANOMALY: MULTI-DATE CONFLICT (Rev 3)

Triggered ONLY when ALL are true:
    - len(date_tags) >= 2
    - pub_date is None
    - override_date is None

Meaning:
Multiple historical publication dates exist, but there is no canonical
pub_date or override_date to declare the current one.
"""

from datetime import date, timedelta

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


TODAY = date.today()
PAST_DATE = TODAY - timedelta(days=90)
MID_DATE = TODAY - timedelta(days=30)
FUTURE_DATE = TODAY + timedelta(days=30)


def _assert_multi_date_conflict(result):
    assert result.status == "anomaly_multi_date_conflict"
    assert result.anomaly_type == "anomaly_multi_date_conflict"


def test_multiple_tags_no_pub_or_override():
    """len(date_tags) >= 2 AND no pub_date AND no override_date → anomaly_multi_date_conflict."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags=[PAST_DATE, FUTURE_DATE],
        pub_date=None,
        override_date=None,
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_multi_date_conflict(result)


def test_not_triggered_when_pub_date_matches_latest_tag():
    """If pub_date exists and equals latest_tag, do NOT classify as anomaly_multi_date_conflict."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags=[PAST_DATE, FUTURE_DATE],
        pub_date=FUTURE_DATE,
        override_date=None,
        inventory=0,
    )
    result = classify_preorder_product(product)

    assert result.status != "anomaly_multi_date_conflict"


def test_not_triggered_when_override_exists():
    """If override_date exists, do NOT classify as anomaly_multi_date_conflict."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags=[PAST_DATE, FUTURE_DATE],
        pub_date=None,
        override_date=FUTURE_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)

    assert result.status != "anomaly_multi_date_conflict"


def test_date_tag_order_does_not_matter():
    """Tag order should not affect conflict detection (sorting must be applied)."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags=[FUTURE_DATE, PAST_DATE],
        pub_date=None,
        override_date=None,
        inventory=5,
    )
    result = classify_preorder_product(product)
    _assert_multi_date_conflict(result)