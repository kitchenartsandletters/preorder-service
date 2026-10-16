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


def test_past_date_with_an_active_hold_stays_active():
    """
    A past pub date keeps a title active only while an explicit hold is live
    (engine._is_held). The hold is set deliberately — a signing, or stock
    genuinely in transit — and carries an end date.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=PAST_DATE,
        inventory=0,
        has_inventory_arrival=False,
        has_active_hold=True,
    )
    result = classify_preorder_product(product)
    assert result.status == "active_preorder"
    assert result.effective_pub_date == PAST_DATE


def test_past_date_without_a_hold_is_not_active():
    """
    Same title with no hold: it is no longer an active preorder. Still in the
    collection, so it reads as stale and needs cleaning up. This retires the
    old inferred "delayed import", which kept such titles active indefinitely.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=PAST_DATE,
        inventory=0,
        has_inventory_arrival=False,
        has_active_hold=False,
    )
    result = classify_preorder_product(product)
    assert result.status != "active_preorder"
    assert result.status == "anomaly_stale_collection"


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
