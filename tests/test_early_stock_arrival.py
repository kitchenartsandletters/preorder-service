"""
EARLY STOCK ARRIVAL TESTS (Rev 3)

A product MUST classify as early_stock_arrival when ALL of the following are true:

    1. effective_pub_date > today
         - derived via override_date
         - OR pub_date
         - OR latest date_tag when no metafields exist

    2. inventory > 0

    3. No anomaly_* conditions match:
         - NOT missing_tag
         - NOT missing_collection
         - NOT pubdate_conflict
         - NOT override_conflict
         - NOT multi_date_conflict

If these conditions are met, the classifier MUST return:
    status="early_stock_arrival"
    anomaly_type=None

This is NOT an anomaly_* status.
It MUST be tested separately from anomaly cases.

TODO CASES TO IMPLEMENT:

1. Future effective_pub_date (override) & inventory > 0 → early_stock_arrival
2. Future effective_pub_date (pub_date) & inventory > 0 → early_stock_arrival
3. Future date_tag only (no metafields) & inventory > 0 → early_stock_arrival
4. Product in preorder collection & future effective date & inventory > 0 → early_stock_arrival
5. Product with preorder tag (no collection) & future effective date & inventory > 0 → early_stock_arrival
6. Ensure early_stock_arrival does NOT classify as active_preorder
      (inventory > 0 prevents active_preorder)
7. Ensure early_stock_arrival does NOT classify as historical_preorder
      (future date prevents historical)
8. Ensure anomalies override early_stock_arrival:
      - if any anomaly_* condition is met → anomaly_* takes priority
      (negative tests)
9. Ensure not_a_preorder_product is NOT returned for these conditions

Imports needed when implementing tests:
    from classification.engine import classify_preorder_product
    from tests.fixtures_product_inputs import make_input

"""
from classification.engine import classify_preorder_product
from tests.fixtures_product_inputs import make_input


from datetime import date, timedelta


TODAY = date.today()
FUTURE_DATE = TODAY + timedelta(days=30)
PAST_DATE = TODAY - timedelta(days=30)


def _assert_early_stock(result):
    assert result.status == "early_stock_arrival"
    assert result.anomaly_type is None


def test_future_override_inventory_positive():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        override_date=FUTURE_DATE,
        inventory=5,
    )
    result = classify_preorder_product(product)
    _assert_early_stock(result)


def test_future_pubdate_inventory_positive():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=FUTURE_DATE,
        inventory=10,
    )
    result = classify_preorder_product(product)
    _assert_early_stock(result)


def test_future_date_tag_only_inventory_positive():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        date_tags=[FUTURE_DATE],
        inventory=3,
    )
    result = classify_preorder_product(product)
    _assert_early_stock(result)


def test_in_preorder_collection_future_date_inventory_positive():
    product = make_input(
        in_preorder_collection=True,
        pub_date=FUTURE_DATE,
        inventory=8,
    )
    result = classify_preorder_product(product)
    # anomaly_missing_tag should override early_stock_arrival
    assert result.status == "anomaly_missing_tag"


def test_preorder_tag_only_future_effective_date_inventory_positive():
    product = make_input(
        tags=["preorder"],
        pub_date=FUTURE_DATE,
        inventory=2,
    )
    result = classify_preorder_product(product)
    # anomaly_missing_collection should override early_stock_arrival
    assert result.status == "anomaly_missing_collection"


def test_inventory_positive_blocks_active_preorder():
    product = make_input(
        pub_date=FUTURE_DATE,
        inventory=4,
    )
    result = classify_preorder_product(product)
    assert result.status != "active_preorder"


def test_future_date_blocks_historical_preorder():
    product = make_input(
        pub_date=FUTURE_DATE,
        inventory=6,
    )
    result = classify_preorder_product(product)
    assert result.status != "historical_preorder"


def test_anomalies_override_early_stock_arrival():
    product = make_input(
        in_preorder_collection=True,
        tags=[],  # triggers anomaly_missing_tag
        pub_date=FUTURE_DATE,
        inventory=5,
    )
    result = classify_preorder_product(product)
    assert result.status.startswith("anomaly_")


def test_not_not_a_preorder_product_for_valid_early_stock():
    product = make_input(
        tags=["preorder"],
        in_preorder_collection=True,
        pub_date=FUTURE_DATE,
        inventory=7,
    )
    result = classify_preorder_product(product)
    assert result.status != "not_a_preorder_product"