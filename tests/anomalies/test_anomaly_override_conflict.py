"""
ANOMALY: OVERRIDE CONFLICT (Rev 3)

Triggered when override_date contradicts known history:

- override_date < pub_date
- OR override_date < latest date_tag
- OR override_date < today while pub_date > today

This anomaly MUST take precedence over active_preorder and early_stock_arrival.
"""

from datetime import date, timedelta

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


TODAY = date.today()
PAST_DATE = TODAY - timedelta(days=30)
FUTURE_DATE = TODAY + timedelta(days=30)


def _assert_override_conflict(result):
    assert result.status == "anomaly_override_conflict"
    assert result.anomaly_type == "anomaly_override_conflict"


def test_override_date_earlier_than_pub_date():
    """override_date < pub_date → anomaly_override_conflict."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=FUTURE_DATE,
        override_date=PAST_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_override_conflict(result)


def test_override_date_earlier_than_latest_tag():
    """override_date < latest_tag → anomaly_override_conflict."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags=[FUTURE_DATE],
        override_date=PAST_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)
    _assert_override_conflict(result)


def test_override_date_earlier_than_today_while_pubdate_future():
    """override_date < today AND pub_date > today → anomaly_override_conflict."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=FUTURE_DATE,
        override_date=PAST_DATE,
        inventory=5,
    )
    result = classify_preorder_product(product)
    _assert_override_conflict(result)


def test_conflict_should_be_pubdate_conflict_not_override_conflict():
    """
    Negative test:
    When pub_date mismatches date_tags but override_date is valid,
    classification should yield anomaly_pubdate_conflict instead.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=FUTURE_DATE,
        date_tags=[PAST_DATE],
        override_date=FUTURE_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)

    assert result.status == "anomaly_pubdate_conflict"
    assert result.anomaly_type == "anomaly_pubdate_conflict"