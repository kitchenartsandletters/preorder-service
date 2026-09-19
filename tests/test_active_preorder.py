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
       EXCEPTION: the delayed-import hold — a past date with the title still in
       the collection, no stock, and no inventory_arrival record stays
       active_preorder (engine._is_delayed_import).

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


def test_past_date_no_arrival_is_delayed_import_active():
    """
    Past pub date + in collection + no stock + NO arrival record is a delayed
    import (stock in transit, commitments open) and is deliberately still
    active_preorder. See engine._is_delayed_import.

    This replaces `test_no_future_date_blocks_active`, which asserted the
    pre-delayed-import rule ("a past date blocks active") and had been failing
    since the delayed-import hold was added.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=PAST_DATE,
        inventory=0,
        has_inventory_arrival=False,
    )
    result = classify_preorder_product(product)
    assert result.status == "active_preorder"
    assert result.effective_pub_date == PAST_DATE


def test_past_date_with_arrival_is_not_active():
    """
    The same title once stock has actually arrived is no longer active: past
    date + still in collection + arrival record -> anomaly_stale_collection.
    Together with the test above, this preserves the original intent (a past
    date blocks active) for every case except the delayed-import hold.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=PAST_DATE,
        inventory=0,
        has_inventory_arrival=True,
    )
    result = classify_preorder_product(product)
    assert result.status != "active_preorder"
    assert result.status == "anomaly_stale_collection"


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
