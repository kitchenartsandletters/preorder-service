"""
The tracking insert must be idempotent per (event_id, line_item_id).

preorder.tracking is one row per LINE ITEM. Until 2026-10-27 its unique key was
event_id alone, which only worked because an empty X-Gateway-Event-ID made this
service mint a fresh UUID per row. When webhook-gateway sent a real, stable
event id, every line after the first violated tracking_event_id_key and the
request 500'd — six failures 21:40-21:43 on 2026-10-26, after which the gateway
disabled the header (commit 0998d48).

Two halves to the fix:
  1. the constraint became UNIQUE NULLS NOT DISTINCT (event_id, line_item_id)
     (migration 2026_10_tracking_unique_event_id_line_item.sql);
  2. the insert gained ON CONFLICT DO NOTHING, so a gateway REDELIVERY of an
     event already stored is a no-op rather than a 500 the gateway retries.

These tests pin the SQL. The constraint itself is enforced by the database.
"""

import inspect
import re

from routes import webhooks


def _insert_sql() -> str:
    """The SQL literal inside _insert_tracking."""
    return inspect.getsource(webhooks._insert_tracking)


def test_insert_targets_event_id_and_line_item_id():
    sql = _insert_sql()
    match = re.search(r"on\s+conflict\s*\(([^)]*)\)", sql, re.IGNORECASE)
    assert match, "the tracking insert must declare an ON CONFLICT target"
    target = {c.strip() for c in match.group(1).split(",")}
    assert target == {"event_id", "line_item_id"}, (
        f"conflict target {target} must match the unique key "
        "(event_id, line_item_id); event_id alone would 500 on multi-line orders"
    )


def test_conflict_action_is_do_nothing():
    """DO UPDATE would let a redelivery overwrite a processed row."""
    sql = _insert_sql()
    assert re.search(r"on\s+conflict\s*\([^)]*\)\s*do\s+nothing", sql, re.IGNORECASE)


def test_insert_still_writes_the_line_item_id():
    """A per-line key is meaningless if the column is not populated."""
    sql = _insert_sql()
    assert "line_item_id" in sql
    assert 'row.get("line_item_id")' in sql


def test_no_plain_insert_into_tracking_elsewhere():
    """
    Any other INSERT into preorder.tracking needs the same conflict handling,
    or the gateway's redeliveries will 500 through that path instead.
    """
    import pathlib

    repo = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in repo.rglob("*.py"):
        if "tests" in path.parts or ".git" in path.parts:
            continue
        text = path.read_text(errors="ignore")
        for match in re.finditer(r"insert\s+into\s+preorder\.tracking", text, re.IGNORECASE):
            tail = text[match.start(): match.start() + 2000]
            if not re.search(r"on\s+conflict", tail, re.IGNORECASE):
                offenders.append(f"{path.relative_to(repo)}")
    assert offenders == [], f"plain INSERT into preorder.tracking in: {offenders}"
