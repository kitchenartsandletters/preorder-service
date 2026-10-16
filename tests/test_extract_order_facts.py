"""
Tests for routes/webhooks._extract_order_facts (docs/DOCS_STATUS.md Landmine 14).

The number of tracking rows written for an event caps how many ledger rows that
event can ever produce: the builder derives a row per line item, but
`ledger_tracking_unique (tracking_id)` admits one ledger row per tracking row
with ON CONFLICT DO NOTHING.

Until 2026-10-16, orders/fulfilled and refunds/create produced a single row for
the whole order, so on a multi-line order only the first line was recorded.
That lost 1,433 of 4,822 fulfilled preorder lines over 12 months and left 68 of
72 open lifecycle snapshots unable to close.
"""

import pytest

from routes.webhooks import _extract_order_facts


def order_payload(*line_items):
    return {
        "id": 5992110063749,
        "name": "#80281",
        "customer": {"id": 42},
        "line_items": list(line_items),
    }


def line(line_id, product_id, qty=1, sku="SKU"):
    return {"id": line_id, "product_id": product_id, "variant_id": product_id + 1,
            "sku": sku, "quantity": qty}


def refund_payload(*refund_lines):
    """payload.id is the REFUND id; the order id lives in order_id."""
    return {
        "id": 999001,
        "order_id": 5992110063749,
        "order": {"name": "#80281", "customer": {"id": 42}},
        "refund_line_items": list(refund_lines),
    }


def refund_line(line_item_id, product_id, qty=1):
    return {
        "line_item_id": line_item_id,
        "quantity": qty,
        "line_item": {"id": line_item_id, "product_id": product_id,
                      "variant_id": product_id + 1, "sku": "SKU"},
    }


@pytest.mark.parametrize("topic", [
    "orders/create",
    "orders/updated",
    "orders/cancelled",
    "orders/paid",
    "orders/fulfilled",
])
def test_every_order_topic_emits_one_row_per_line(topic):
    """orders/paid and orders/fulfilled were the regression: one row per ORDER."""
    facts = _extract_order_facts(topic, order_payload(line(1, 111), line(2, 222), line(3, 333)))

    assert len(facts) == 3
    assert [f["line_item_id"] for f in facts] == [1, 2, 3]
    assert [f["product_id"] for f in facts] == [111, 222, 333]
    assert {f["order_id"] for f in facts} == {5992110063749}
    assert {f["order_name"] for f in facts} == {"#80281"}
    assert {f["status"] for f in facts} == {"pending"}


def test_single_line_order_still_emits_one_row():
    facts = _extract_order_facts("orders/fulfilled", order_payload(line(1, 111, qty=2)))
    assert len(facts) == 1
    assert facts[0]["quantity"] == 2


def test_refund_emits_one_row_per_refund_line():
    facts = _extract_order_facts("refunds/create", refund_payload(
        refund_line(1, 111, qty=1), refund_line(2, 222, qty=3)
    ))

    assert len(facts) == 2
    assert [f["line_item_id"] for f in facts] == [1, 2]
    assert [f["quantity"] for f in facts] == [1, 3]


def test_refund_order_id_is_the_order_not_the_refund():
    """payload.id is the refund id; using it as order_id corrupted the ledger before."""
    facts = _extract_order_facts("refunds/create", refund_payload(refund_line(1, 111)))

    assert facts[0]["order_id"] == 5992110063749
    assert facts[0]["order_id"] != 999001


def test_refund_falls_back_to_nested_order_id():
    payload = {
        "id": 999002,
        "order": {"id": 777, "name": "#80999"},
        "refund_line_items": [refund_line(5, 555)],
    }
    facts = _extract_order_facts("refunds/create", payload)
    assert facts[0]["order_id"] == 777


@pytest.mark.parametrize("topic", ["products/update", "inventory_levels/update"])
def test_product_and_inventory_topics_emit_exactly_one_row(topic):
    """These carry no line items; one row per event is correct."""
    facts = _extract_order_facts(topic, {"id": 1})
    assert facts == [{"status": "pending"}]


@pytest.mark.parametrize("payload", [{}, {"line_items": []}, {"refund_line_items": []}])
def test_empty_payload_still_records_the_event(payload):
    """A fallback row keeps the event in tracking rather than dropping it."""
    for topic in ("orders/fulfilled", "refunds/create", "orders/create"):
        facts = _extract_order_facts(topic, payload)
        assert facts == [{"status": "pending"}]


def test_unknown_topic_records_one_row():
    assert _extract_order_facts("orders/edited", {"id": 1}) == [{"status": "pending"}]
