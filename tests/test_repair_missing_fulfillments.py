"""
Tests for scripts/repair_missing_fulfillments.py (repair R3).

This script writes production data that subtracts customer commitments, so the
tests are adversarial. The central one is
`test_payload_yields_exactly_one_correct_ledger_row`, which runs the REAL
builder (`build_commitment_ledger.extract_ledger_rows`) over the synthesized
payload — the only way to know the payload means what it is meant to mean,
rather than what this script believes it means.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import build_commitment_ledger as builder

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "repair_missing_fulfillments.py"

spec = importlib.util.spec_from_file_location("repair_missing_fulfillments", SCRIPT)
repair = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = repair  # @dataclass needs the module registered first
spec.loader.exec_module(repair)

PRODUCT_ID = 6598890029189
ORDER_ID = 5730407219333
LINE_ID = 13825650131077
FULFILLMENT_ID = 4455667788
VARIANT_ID = 42


def order(
    *,
    cancelled=None,
    line_id=LINE_ID,
    unfulfilled=0,
    product_id=PRODUCT_ID,
    fulfillment_lines=None,
    fulfillments=None,
    name="#70810",
):
    if fulfillments is None:
        fulfillments = [
            {
                "id": f"gid://shopify/Fulfillment/{FULFILLMENT_ID}",
                "createdAt": "2026-02-20T15:04:05Z",
                "status": "SUCCESS",
                "fulfillmentLineItems": {
                    "nodes": fulfillment_lines
                    if fulfillment_lines is not None
                    else [{"quantity": 1, "lineItem": {"id": f"gid://shopify/LineItem/{line_id}"}}]
                },
            }
        ]
    return {
        "id": f"gid://shopify/Order/{ORDER_ID}",
        "name": name,
        "cancelledAt": cancelled,
        "lineItems": {
            "nodes": [
                {
                    "id": f"gid://shopify/LineItem/{line_id}",
                    "quantity": 1,
                    "currentQuantity": 1,
                    "unfulfilledQuantity": unfulfilled,
                    "product": {"id": f"gid://shopify/Product/{product_id}"},
                    "variant": {"id": f"gid://shopify/ProductVariant/{VARIANT_ID}"},
                }
            ]
        },
        "fulfillments": fulfillments,
    }


def candidate(order_id=ORDER_ID, line_item_id=LINE_ID, net=1, verdict="fulfilled"):
    return {"order_id": order_id, "line_item_id": line_item_id, "net": net, "verdict": verdict}


# --------------------------------------------------------------------------
# THE important test: does the builder read the payload as intended?
# --------------------------------------------------------------------------

def test_payload_yields_exactly_one_correct_ledger_row():
    plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: order()})
    assert plan.refusals == []
    assert len(plan.rows) == 1

    tracking = plan.rows[0].tracking_row("shop.myshopify.com", "2026-11-24")
    rows = builder.extract_ledger_rows(
        tracking_id="11111111-1111-1111-1111-111111111111",
        event_id=tracking["event_id"],
        topic="orders/fulfilled",
        payload=tracking["payload"],
        created_at=datetime(2026, 11, 24, tzinfo=timezone.utc),
    )

    # ledger_tracking_unique is UNIQUE on commitment_ledger.tracking_id, so a
    # tracking row that produced more than one would silently drop all but one.
    assert len(rows) == 1, "one tracking row must yield exactly one ledger row"
    r = rows[0]
    assert r.topic == "orders/fulfilled"
    assert r.product_id == PRODUCT_ID
    assert r.order_id == ORDER_ID
    assert r.line_item_id == LINE_ID
    assert r.variant_id == VARIANT_ID
    assert r.delta_qty == -1, "a fulfillment must SUBTRACT commitment"
    assert r.fulfillment_id == FULFILLMENT_ID, "the dedupe key must survive into the ledger"


def test_occurred_at_is_the_fulfillment_time_not_the_order_time():
    """pick_occurred_at tries top-level created_at FIRST for orders/fulfilled.

    If the payload carried the order's created_at, every repaired fulfillment
    would be dated at order time, which would land it before the pub-date
    boundary and corrupt the frozen presale cohort.
    """
    plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: order()})
    payload = plan.rows[0].tracking_row("s", "2026-11-24")["payload"]
    assert "created_at" not in payload

    occurred = builder.pick_occurred_at(
        "orders/fulfilled", payload, datetime(2000, 1, 1, tzinfo=timezone.utc)
    )
    assert occurred.year == 2026 and occurred.month == 2 and occurred.day == 20


def test_builder_does_not_fall_back_to_the_tracking_created_at():
    plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: order()})
    payload = plan.rows[0].tracking_row("s", "2026-11-24")["payload"]
    fallback = datetime(2030, 6, 6, tzinfo=timezone.utc)
    assert builder.pick_occurred_at("orders/fulfilled", payload, fallback) != fallback


# --------------------------------------------------------------------------
# Refusals — each one is a way to subtract a commitment that is still owed
# --------------------------------------------------------------------------

def test_refuses_a_line_still_unfulfilled_in_shopify():
    plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: order(unfulfilled=1)})
    assert plan.rows == []
    assert any("still has 1 unfulfilled" in r for r in plan.refusals)


def test_refuses_a_cancelled_order():
    plan = repair.build_plan(
        PRODUCT_ID, [candidate()], {ORDER_ID: order(cancelled="2026-03-01T00:00:00Z")}
    )
    assert plan.rows == []
    assert any("cancelled in Shopify" in r for r in plan.refusals)


def test_refuses_a_missing_order():
    plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: None})
    assert plan.rows == []
    assert any("not found in Shopify" in r for r in plan.refusals)


def test_refuses_a_line_that_left_the_order():
    plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: order(line_id=999)})
    assert plan.rows == []
    assert any("not on the" in r for r in plan.refusals)


def test_refuses_a_line_belonging_to_another_product():
    plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: order(product_id=111)})
    assert plan.rows == []
    assert any("belongs to product" in r for r in plan.refusals)


def test_refuses_when_no_fulfillment_contains_the_line():
    """Without a real fulfillment id the dedupe index cannot protect a re-run,
    so inventing one is worse than refusing."""
    plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: order(fulfillment_lines=[])})
    assert plan.rows == []
    assert any("no fulfillment in Shopify contains it" in r for r in plan.refusals)


def test_refuses_when_the_fulfillment_id_is_unparseable():
    """A fulfillment whose id cannot be read must be skipped, not given a
    placeholder. fulfillment_id is two thirds of cl_unique_orders_fulfilled, so
    a fabricated value writes an untraceable row and weakens the one index that
    makes a re-run safe.
    """
    fulfillments = [{
        "id": "gid://shopify/Fulfillment/not-a-number",
        "createdAt": "2026-02-20T00:00:00Z",
        "fulfillmentLineItems": {"nodes": [
            {"quantity": 1, "lineItem": {"id": f"gid://shopify/LineItem/{LINE_ID}"}}]},
    }]
    plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: order(fulfillments=fulfillments)})
    assert plan.rows == []
    assert any("no fulfillment in Shopify contains it" in r for r in plan.refusals)


def test_refuses_a_zero_quantity_fulfillment_line():
    plan = repair.build_plan(
        PRODUCT_ID,
        [candidate()],
        {ORDER_ID: order(fulfillment_lines=[{"quantity": 0,
                                             "lineItem": {"id": f"gid://shopify/LineItem/{LINE_ID}"}}])},
    )
    assert plan.rows == []


def test_only_fulfilled_verdicts_are_candidates():
    data = {"product_id": PRODUCT_ID, "lines": [
        candidate(verdict="open"),
        candidate(verdict="fulfilled", line_item_id=222),
        candidate(verdict="cancelled", line_item_id=333),
    ]}
    path = Path("/tmp/_r3_test.json")
    path.write_text(json.dumps(data))
    pid, cands = repair.load_candidates(path)
    assert pid == PRODUCT_ID
    assert [c["line_item_id"] for c in cands] == [222]
    path.unlink()


# --------------------------------------------------------------------------
# Split shipments
# --------------------------------------------------------------------------

def test_a_line_split_across_two_fulfillments_yields_one_row_each():
    fulfillments = [
        {
            "id": "gid://shopify/Fulfillment/1",
            "createdAt": "2026-02-20T00:00:00Z",
            "fulfillmentLineItems": {"nodes": [
                {"quantity": 1, "lineItem": {"id": f"gid://shopify/LineItem/{LINE_ID}"}}]},
        },
        {
            "id": "gid://shopify/Fulfillment/2",
            "createdAt": "2026-03-05T00:00:00Z",
            "fulfillmentLineItems": {"nodes": [
                {"quantity": 2, "lineItem": {"id": f"gid://shopify/LineItem/{LINE_ID}"}}]},
        },
    ]
    plan = repair.build_plan(
        PRODUCT_ID, [candidate(net=3)], {ORDER_ID: order(fulfillments=fulfillments)}
    )
    assert plan.refusals == []
    assert [(r.fulfillment_id, r.quantity) for r in plan.rows] == [(1, 1), (2, 2)]
    # Distinct fulfillment ids mean distinct event ids, so both tracking rows land.
    assert len({r.event_id for r in plan.rows}) == 2


def test_fulfillments_as_a_plain_list_and_as_a_connection_both_parse():
    conn = order()
    plain = order()
    plain["fulfillments"] = conn["fulfillments"]
    flat = order()
    flat["fulfillments"] = [dict(f, fulfillmentLineItems=f["fulfillmentLineItems"]["nodes"])
                            for f in conn["fulfillments"]]
    for shape in (plain, flat):
        plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: shape})
        assert len(plan.rows) == 1, "both fulfillment shapes must parse"


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------

def test_event_id_is_deterministic():
    a = repair.deterministic_event_id(1, 2, 3)
    b = repair.deterministic_event_id(1, 2, 3)
    assert a == b


def test_event_id_varies_with_every_component():
    base = repair.deterministic_event_id(1, 2, 3)
    assert repair.deterministic_event_id(9, 2, 3) != base
    assert repair.deterministic_event_id(1, 9, 3) != base
    assert repair.deterministic_event_id(1, 2, 9) != base


def test_event_id_is_a_uuid():
    import uuid as _uuid
    _uuid.UUID(repair.deterministic_event_id(1, 2, 3))


# --------------------------------------------------------------------------
# Totals guard
# --------------------------------------------------------------------------

def test_guard_refuses_subtracting_more_than_was_open():
    """The only truly dangerous arithmetic error: over-subtracting."""
    fulfillments = [{
        "id": "gid://shopify/Fulfillment/1",
        "createdAt": "2026-02-20T00:00:00Z",
        "fulfillmentLineItems": {"nodes": [
            {"quantity": 5, "lineItem": {"id": f"gid://shopify/LineItem/{LINE_ID}"}}]},
    }]
    cands = [candidate(net=1)]
    plan = repair.build_plan(PRODUCT_ID, cands, {ORDER_ID: order(fulfillments=fulfillments)})
    problems = repair.check_totals(plan, cands)
    assert any("never subtract more than was open" in p for p in problems)


def test_guard_passes_when_the_plan_matches():
    cands = [candidate()]
    plan = repair.build_plan(PRODUCT_ID, cands, {ORDER_ID: order()})
    assert repair.check_totals(plan, cands) == []


def test_guard_allows_subtracting_less_than_was_open():
    """Fewer units than the JSON showed is fine — a line may have been
    repaired, or one of several lines refused."""
    cands = [candidate(net=1), candidate(line_item_id=777, net=1)]
    plan = repair.build_plan(PRODUCT_ID, [cands[0]], {ORDER_ID: order()})
    assert repair.check_totals(plan, cands) == []


# --------------------------------------------------------------------------
# Row shape
# --------------------------------------------------------------------------

def test_tracking_row_carries_exactly_one_line_item():
    plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: order()})
    payload = plan.rows[0].tracking_row("s", "2026-11-24")["payload"]
    assert len(payload["line_items"]) == 1


def test_tracking_row_is_marked_as_a_repair_and_names_its_source():
    plan = repair.build_plan(PRODUCT_ID, [candidate()], {ORDER_ID: order()})
    row = plan.rows[0].tracking_row("s", "2026-11-24")
    assert row["processing_notes"].startswith("repair 2026-11-24: R3")
    assert str(FULFILLMENT_ID) in row["processing_notes"]
    assert row["source_service"] == "repair-r3"
    assert row["topic"] == "orders/fulfilled"


def test_numeric_id_is_exact_not_substring():
    assert repair.numeric_id("gid://shopify/LineItem/101") == 101
    assert repair.numeric_id("gid://shopify/LineItem/abc") is None
    assert repair.numeric_id(None) is None


def test_dotenv_is_not_loaded_at_import_time():
    import ast

    tree = ast.parse(SCRIPT.read_text())
    module_calls = [
        n.value.func.id
        for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
    ]
    assert "load_dotenv" not in module_calls
    assert "load_dotenv(" in SCRIPT.read_text()
