"""
tests/fakes.py

Shared in-memory stand-in for the supabase-py client, used by tests that
exercise code which talks to Supabase (orchestrator, persistence, pub-date
history).

Why this exists
---------------
Earlier tests each hand-rolled a fake or used MagicMock, and they drifted
silently when production switched to `supabase.schema("preorder").table(...)`:
MagicMock accepted the new chain, recorded it somewhere nobody asserted on, and
the tests failed (or, worse, passed while asserting nothing). This fake:

- Mirrors the call chains production actually uses:
  `.schema(name).table(name)`, `.select()`, `.eq()`, `.neq()`, `.limit()`,
  `.order()`, `.single()`, `.maybe_single()`, `.insert()`, `.upsert(on_conflict=)`,
  `.update()`, `.delete()`, `.execute()`.
- Stores rows per (schema, table), so tests assert on resulting STATE
  (what is in `preorder.pubdate_history`) instead of mock-call trivia.
- Raises on an unmodelled method (plain attribute access), so a production
  change to a new query shape fails loudly here instead of passing vacuously.

`.single()` / `.maybe_single()` semantics follow supabase-py 2.x closely enough
for our call sites: `maybe_single()` yields `data=None` when no row matches;
`single()` raises when the match count is not exactly one.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class FakeResponse:
    data: Any = None
    count: Optional[int] = None


@dataclass
class _Query:
    db: "FakeSupabase"
    schema: Optional[str]
    table: str
    op: str = "select"
    payload: Any = None
    on_conflict: Optional[str] = None
    filters: List[Tuple[str, str, Any]] = field(default_factory=list)
    limit_n: Optional[int] = None
    single_mode: Optional[str] = None  # None | "single" | "maybe_single"
    order_by: Optional[Tuple[str, bool]] = None

    # ---- builders -------------------------------------------------------
    def select(self, *_cols, **_kw):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict: Optional[str] = None, **_kw):
        self.op, self.payload, self.on_conflict = "upsert", payload, on_conflict
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self.filters.append(("neq", col, val))
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def order(self, col, desc: bool = False, **_kw):
        self.order_by = (col, desc)
        return self

    def single(self):
        self.single_mode = "single"
        return self

    def maybe_single(self):
        self.single_mode = "maybe_single"
        return self

    # ---- execution ------------------------------------------------------
    def _matches(self, row: Dict[str, Any]) -> bool:
        for kind, col, val in self.filters:
            have = row.get(col)
            if kind == "eq" and have != val:
                return False
            if kind == "neq" and have == val:
                return False
        return True

    def execute(self) -> FakeResponse:
        key = (self.schema, self.table)
        rows = self.db.tables.setdefault(key, [])
        self.db.calls.append(
            {
                "schema": self.schema,
                "table": self.table,
                "op": self.op,
                "payload": copy.deepcopy(self.payload),
                "on_conflict": self.on_conflict,
                "filters": list(self.filters),
            }
        )

        if self.op == "select":
            found = [copy.deepcopy(r) for r in rows if self._matches(r)]
            if self.order_by:
                col, desc = self.order_by
                found.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
            if self.limit_n is not None:
                found = found[: self.limit_n]
            if self.single_mode == "maybe_single":
                return FakeResponse(data=found[0] if found else None)
            if self.single_mode == "single":
                if len(found) != 1:
                    raise RuntimeError(
                        f"single() expected exactly 1 row from {key}, got {len(found)}"
                    )
                return FakeResponse(data=found[0])
            return FakeResponse(data=found)

        payloads = self.payload if isinstance(self.payload, list) else [self.payload]

        if self.op == "insert":
            for p in payloads:
                rows.append(copy.deepcopy(p))
            return FakeResponse(data=copy.deepcopy(payloads))

        if self.op == "upsert":
            conflict_cols = [c.strip() for c in (self.on_conflict or "").split(",") if c.strip()]
            for p in payloads:
                existing = None
                if conflict_cols:
                    for r in rows:
                        if all(r.get(c) == p.get(c) for c in conflict_cols):
                            existing = r
                            break
                if existing is not None:
                    existing.update(copy.deepcopy(p))
                else:
                    rows.append(copy.deepcopy(p))
            return FakeResponse(data=copy.deepcopy(payloads))

        if self.op == "update":
            updated = []
            for r in rows:
                if self._matches(r):
                    r.update(copy.deepcopy(self.payload))
                    updated.append(copy.deepcopy(r))
            return FakeResponse(data=updated)

        if self.op == "delete":
            kept = [r for r in rows if not self._matches(r)]
            removed = [r for r in rows if self._matches(r)]
            self.db.tables[key] = kept
            return FakeResponse(data=removed)

        raise NotImplementedError(f"FakeSupabase: unsupported op {self.op!r}")


class _SchemaScope:
    def __init__(self, db: "FakeSupabase", schema: str):
        self._db, self._schema = db, schema

    def table(self, name: str) -> _Query:
        return _Query(db=self._db, schema=self._schema, table=name)


class FakeSupabase:
    """In-memory supabase-py stand-in. See module docstring."""

    def __init__(self):
        self.tables: Dict[Tuple[Optional[str], str], List[Dict[str, Any]]] = {}
        self.calls: List[Dict[str, Any]] = []

    # production path
    def schema(self, name: str) -> _SchemaScope:
        return _SchemaScope(self, name)

    # unscoped path (kept so a regression to the old call style is visible in
    # `calls` with schema=None rather than crashing obscurely)
    def table(self, name: str) -> _Query:
        return _Query(db=self, schema=None, table=name)

    # ---- test helpers ---------------------------------------------------
    def seed(self, schema: str, table: str, rows: List[Dict[str, Any]]) -> None:
        self.tables.setdefault((schema, table), []).extend(copy.deepcopy(rows))

    def rows(self, schema: str, table: str) -> List[Dict[str, Any]]:
        return copy.deepcopy(self.tables.get((schema, table), []))

    def calls_to(self, schema: Optional[str], table: str, op: Optional[str] = None):
        return [
            c
            for c in self.calls
            if c["schema"] == schema and c["table"] == table and (op is None or c["op"] == op)
        ]
