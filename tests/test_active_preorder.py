"""
ACTIVE PREORDER TESTS (Rev 4 — Strict Structural Alignment)

ACTIVE PREORDER REQUIRES:

    1. Structural alignment:
        - 'preorder' in tags
        - in_preorder_collection == True

    2. A valid FUTURE effective_pub_date
        - override_date OR
        - pub_date OR
        - latest date_tag

    3. inventory <= 0

    4. No anomaly_* conditions apply
    5. Not early_stock_arrival (inventory must be <= 0)

Anything less than the above is NOT active_preorder.
"""

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input

from datetime import date, timedelta

FUTURE_DATE = date.today() + timedelta(days=30)
PAST_DATE = date.today() - timedelta(days=30)


# -----------------------------
# VALID ACTIVE PREORDER CASES
# -----------------------------


def test_tag_and_collection_future_pub_date_inventory_zero():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=FUTURE_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)
    assert result.status == "active_preorder"


def test_tag_and_collection_future_pub_date_inventory_negative():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=FUTURE_DATE,
        inventory=-5,
    )
    result = classify_preorder_product(product)
    assert result.status == "active_preorder"


def test_tag_and_collection_future_date_tag_only_inventory_zero():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags=[FUTURE_DATE],
        pub_date=None,
        override_date=None,
        inventory=0,
    )
    result = classify_preorder_product(product)
    assert result.status == "active_preorder"


def test_tag_and_collection_future_override_inventory_zero():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        override_date=FUTURE_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)
    assert result.status == "active_preorder"


# -----------------------------
# INVALID / GUARD CASES
# -----------------------------


def test_missing_collection_blocks_active():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,
        pub_date=FUTURE_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)
    assert result.status == "anomaly_missing_collection"


def test_missing_tag_blocks_active():
    product = make_input(
        tags=[],
        in_preorder_collection=True,
        pub_date=FUTURE_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)
    assert result.status == "anomaly_missing_tag"


def test_no_future_date_blocks_active():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=PAST_DATE,
        inventory=0,
    )
    result = classify_preorder_product(product)
    assert result.status != "active_preorder"


def test_no_date_metadata_blocks_active():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=None,
        override_date=None,
        date_tags=[],
        inventory=0,
    )
    result = classify_preorder_product(product)
    assert result.status != "active_preorder"