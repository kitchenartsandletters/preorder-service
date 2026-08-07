"""
ANOMALY: STALE COLLECTION vs DELAYED IMPORT

anomaly_stale_collection fires only when a title's pub date has passed, it is
still in the preorder collection, AND stock actually arrived
(has_inventory_arrival=True). That combination means the book released and was
mistakenly left in the collection — a cleanup oversight requiring human action.

A past-pub title still in the collection with NO inventory arrival is a delayed
import (e.g. shipped from overseas, delayed in transit). Customers have ordered
and are waiting, so it must remain active_preorder — NOT an anomaly, and NOT
historical (which would imply release). It transitions to historical only once
stock lands and inventory_arrival fires.
"""

from datetime import date, timedelta

from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


PAST_DATE = date.today() - timedelta(days=7)
FUTURE_DATE = date.today() + timedelta(days=30)


# ──────────────────────────────────────────────
# anomaly_stale_collection — fires only with inventory arrival
# ──────────────────────────────────────────────

def test_stale_collection_fires_when_stock_arrived():
    """
    Past pub date, still in collection, has preorder tag, stock arrived.
    The book released and was left in the collection → anomaly_stale_collection.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=PAST_DATE,
        date_tags=[PAST_DATE],
        inventory=0,
        has_inventory_arrival=True,
    )
    result = classify_preorder_product(product)
    assert result.status == "anomaly_stale_collection"
    assert result.anomaly_type == "anomaly_stale_collection"


def test_stale_collection_does_not_fire_for_future_pub():
    """Still in collection with a future pub date is a normal active preorder."""
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=FUTURE_DATE,
        date_tags=[FUTURE_DATE],
        inventory=0,
        has_inventory_arrival=False,
    )
    result = classify_preorder_product(product)
    assert result.status != "anomaly_stale_collection"


# ──────────────────────────────────────────────
# Delayed import — held as active_preorder, not an anomaly
# ──────────────────────────────────────────────

def test_delayed_import_holds_as_active_preorder():
    """
    Past pub date, still in collection, preorder tag, NO inventory arrival,
    negative inventory (open commitments). This is a delayed import in transit —
    it must stay active_preorder, not become an anomaly or historical.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=PAST_DATE,
        date_tags=[PAST_DATE],
        inventory=-53,
        has_inventory_arrival=False,
    )
    result = classify_preorder_product(product)
    assert result.status == "active_preorder"
    assert result.anomaly_type is None


def test_delayed_import_zero_inventory_still_active():
    """
    Past pub date, in collection, no arrival, inventory exactly 0.
    Still a delayed import (no stock ever recorded) → active_preorder.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=PAST_DATE,
        date_tags=[PAST_DATE],
        inventory=0,
        has_inventory_arrival=False,
    )
    result = classify_preorder_product(product)
    assert result.status == "active_preorder"
    assert result.anomaly_type is None


def test_delayed_import_transitions_once_stock_arrives():
    """
    When the delayed import's stock finally lands (has_inventory_arrival=True)
    and the collection is cleaned up, it becomes historical_preorder and thus
    reportable. This models the post-arrival reclassification.
    """
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=False,   # removed from collection after arrival
        pub_date=PAST_DATE,
        date_tags=[PAST_DATE],
        inventory=0,
        has_inventory_arrival=True,
    )
    result = classify_preorder_product(product)
    assert result.status == "historical_preorder"
    assert result.anomaly_type is None
