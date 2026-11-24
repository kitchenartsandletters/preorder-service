"""
Tests for scripts/verify_open_commitment_lines.py.

The script is read-only, but its arithmetic decides how many books get sent to
customers, so the netting and the verdicts are tested directly. Each guard here
was written against a mutation that would otherwise pass silently.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "verify_open_commitment_lines.py"

spec = importlib.util.spec_from_file_location("verify_open_commitment_lines", SCRIPT)
verify = importlib.util.module_from_spec(spec)
# @dataclass resolves annotations through sys.modules[cls.__module__], so the
# module must be registered BEFORE exec_module or OpenLine fails to build.
sys.modules[spec.name] = verify
spec.loader.exec_module(verify)


def row(order_id, line_item_id, topic, delta, occurred_at="2026-01-01T00:00:00+00:00"):
    return {
        "order_id": order_id,
        "line_item_id": line_item_id,
        "topic": topic,
        "delta_qty": delta,
        "occurred_at": occurred_at,
    }


# --------------------------------------------------------------------------
# Netting
# --------------------------------------------------------------------------

def test_open_line_survives_when_only_a_create_exists():
    lines = verify.compute_open_lines([row(1, 10, "orders/create", 1)])
    assert [(l.order_id, l.line_item_id, l.net) for l in lines] == [(1, 10, 1)]


def test_create_plus_fulfillment_nets_to_closed():
    lines = verify.compute_open_lines(
        [row(1, 10, "orders/create", 1), row(1, 10, "orders/fulfilled", -1)]
    )
    assert lines == []


def test_refund_closes_a_line():
    lines = verify.compute_open_lines(
        [row(1, 10, "orders/create", 1), row(1, 10, "refunds/create", -1)]
    )
    assert lines == []


def test_orphan_fulfillment_is_not_reported_as_open():
    """A fulfillment with no create nets negative. It is residue, not demand."""
    lines = verify.compute_open_lines([row(1, 10, "orders/fulfilled", -1)])
    assert lines == []


def test_lines_are_netted_independently_within_one_order():
    """Multi-line orders: one shipped line must not close its siblings.

    This is the Landmine 14 shape; netting per order instead of per line would
    hide real demand.
    """
    lines = verify.compute_open_lines(
        [
            row(1, 10, "orders/create", 1),
            row(1, 11, "orders/create", 1),
            row(1, 10, "orders/fulfilled", -1),
        ]
    )
    assert [(l.line_item_id, l.net) for l in lines] == [(11, 1)]


def test_rows_without_order_identity_are_ignored():
    """reconciliation.adjustment and create_backfill rows carry no order id."""
    lines = verify.compute_open_lines(
        [
            {"order_id": None, "line_item_id": None, "topic": "orders/create",
             "delta_qty": 9, "occurred_at": "2026-01-01T00:00:00+00:00"},
            row(1, 10, "orders/create", 1),
        ]
    )
    assert [(l.order_id, l.net) for l in lines] == [(1, 1)]


def test_first_seen_is_the_earliest_not_the_last():
    lines = verify.compute_open_lines(
        [
            row(1, 10, "orders/create", 1, "2026-05-05T00:00:00+00:00"),
            row(1, 10, "orders/create", 0, "2026-01-01T00:00:00+00:00"),
        ]
    )
    assert lines[0].first_seen.startswith("2026-01-01")


def test_output_is_ordered_oldest_first():
    lines = verify.compute_open_lines(
        [
            row(2, 20, "orders/create", 1, "2026-06-01T00:00:00+00:00"),
            row(1, 10, "orders/create", 1, "2025-12-02T00:00:00+00:00"),
        ]
    )
    assert [l.order_id for l in lines] == [1, 2]


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

def order(line_items, **kwargs):
    base = {
        "id": "gid://shopify/Order/1",
        "name": "#1001",
        "cancelledAt": None,
        "displayFulfillmentStatus": "UNFULFILLED",
        "displayFinancialStatus": "PAID",
        "lineItems": {"nodes": line_items},
    }
    base.update(kwargs)
    return base


def line(line_item_id, quantity=1, current=1, unfulfilled=1):
    return {
        "id": f"gid://shopify/LineItem/{line_item_id}",
        "quantity": quantity,
        "currentQuantity": current,
        "unfulfilledQuantity": unfulfilled,
        "refundableQuantity": current,
    }


def test_unfulfilled_line_is_open():
    verdict, detail = verify.classify_line(order([line(10)]), 10)
    assert verdict == "open"
    assert detail["order_name"] == "#1001"


def test_cancelled_order_beats_everything_else():
    o = order([line(10)], cancelledAt="2026-02-01T00:00:00Z")
    assert verify.classify_line(o, 10)[0] == "cancelled"


def test_fully_fulfilled_line_is_not_open():
    assert verify.classify_line(order([line(10, unfulfilled=0)]), 10)[0] == "fulfilled"


def test_refunded_line_is_not_open_even_while_unfulfilled():
    """currentQuantity 0 with unfulfilledQuantity 1 is a refunded line.

    Checking unfulfilledQuantity alone would call this a live commitment and
    ship a book to someone who was refunded.
    """
    assert verify.classify_line(order([line(10, current=0, unfulfilled=1)]), 10)[0] == "refunded"


def test_missing_line_is_flagged():
    assert verify.classify_line(order([line(99)]), 10)[0] == "line_missing"


def test_missing_order_is_flagged():
    assert verify.classify_line(None, 10)[0] == "order_missing"


def test_line_matching_is_exact_not_prefix():
    """gid 101 must not satisfy a lookup for line 10."""
    assert verify.classify_line(order([line(101)]), 10)[0] == "line_missing"


@pytest.mark.parametrize(
    "gid,expected",
    [
        ("gid://shopify/LineItem/123", 123),
        ("gid://shopify/LineItem/123/", 123),
        ("", None),
        ("gid://shopify/LineItem/abc", None),
    ],
)
def test_line_numeric_id(gid, expected):
    assert verify._line_numeric_id(gid) == expected


# --------------------------------------------------------------------------
# Paging
# --------------------------------------------------------------------------

class FakeQuery:
    def __init__(self, pages):
        self._pages = pages
        self._page = 0

    def schema(self, _):
        return self

    def table(self, _):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, start, _end):
        self._page = start // verify.PAGE_SIZE
        return self

    def execute(self):
        class R:
            pass

        r = R()
        r.data = self._pages[self._page] if self._page < len(self._pages) else []
        return r


def test_fetch_ledger_rows_pages_past_the_postgrest_cap():
    """A single unpaged select caps at 1000 rows and silently truncated a
    migration in this repo before (Move 1d). Two full pages plus a short one.
    """
    full = [row(i, i, "orders/create", 1) for i in range(verify.PAGE_SIZE)]
    tail = [row(999_001, 999_001, "orders/create", 1)]
    fake = FakeQuery([full, full, tail])

    rows = verify.fetch_ledger_rows(fake, product_id=1)
    assert len(rows) == verify.PAGE_SIZE * 2 + 1


def test_fetch_ledger_rows_refuses_to_truncate_silently():
    full = [row(i, i, "orders/create", 1) for i in range(verify.PAGE_SIZE)]
    fake = FakeQuery([full] * (verify.MAX_PAGES + 1))
    with pytest.raises(RuntimeError, match="raise MAX_PAGES"):
        verify.fetch_ledger_rows(fake, product_id=1)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def test_render_separates_real_demand_from_residue():
    lines = [
        verify.OpenLine(1, 10, 1, "2025-12-02T00:00:00+00:00", "open", {"order_name": "#1"}),
        verify.OpenLine(2, 20, 1, "2026-01-02T00:00:00+00:00", "cancelled", {"order_name": "#2"}),
        verify.OpenLine(3, 30, 1, "2026-01-03T00:00:00+00:00", "fulfilled", {"order_name": "#3"}),
    ]
    out = verify.render(lines)
    assert "FULFIL 1 unit(s). 2 unit(s) of ledger residue" in out
    assert "open" in out and "cancelled" in out


def test_render_counts_unknown_verdicts_too():
    lines = [verify.OpenLine(1, 10, 3, "2026-01-01T00:00:00+00:00", "unchecked", {})]
    out = verify.render(lines)
    assert "unchecked" in out
    assert "FULFIL 0 unit(s). 3 unit(s) of ledger residue" in out
