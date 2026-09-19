"""
Lifecycle snapshotter tests (lifecycle_snapshotter.run_daily).

Pins the CURRENT rules (verified against lifecycle_snapshotter.py):

Snapshot creation
- Candidates are LEDGER-DRIVEN: a product needs commitment_ledger activity,
  a product_status row with status in (active_preorder, historical_preorder),
  a non-null effective_pub_date <= today (ET), and no existing snapshot.
- A status safety-guard re-reads product_status before inserting.
- The frozen presale cohort = net ledger deltas strictly before ET midnight of
  effective_pub_date, counting only clean topics
  ('orders/create', 'orders/fulfilled', 'refunds/create').
- Insert is idempotent (on conflict do nothing): one row per product.

Closure (Phase 13 proxy)
- Close only if an inventory_arrival record exists AND current clean-topic
  commitment is <= 0.

Why this file was rewritten
- The previous FakePool matched query text production no longer issues
  (candidates came from `from preorder.product_status`; committed qty matched
  `sum(delta_qty)`; no status-guard or topic handling), so it returned no
  candidates and every test failed.
- `test_trivial_zero_presale_closes` asserted behavior production deliberately
  no longer has: a product with no ledger activity is never a candidate, and
  closure requires an arrival record. It is replaced by
  `test_no_ledger_activity_creates_no_snapshot` and
  `test_zero_commit_without_arrival_stays_open`.

This FakePool RAISES on any query it does not model, so future query changes
fail loudly here instead of silently returning empty results.
"""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import lifecycle_snapshotter as ls

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

PREORDER_STATUSES = {"active_preorder", "historical_preorder"}


def et_midnight_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 0, 0, tzinfo=ET).astimezone(UTC)


class FakePool:
    """asyncpg-pool stand-in modelling exactly the queries run_daily issues."""

    def __init__(self):
        self.product_status = {}      # pid -> {"effective_pub_date": date, "status": str}
        self.commitment_ledger = []   # {"product_id","delta_qty","occurred_at","topic"}
        self.inventory_arrival = {}   # pid -> first_positive_inventory_at
        self.lifecycle_snapshot = {}  # pid -> row dict

    # ---- helpers --------------------------------------------------------
    @staticmethod
    def _topics_in(q):
        """
        Topic filter as written in the query text (`topic in ('a', 'b')`), or
        None if the query has no topic filter. Deriving it from the query —
        rather than hard-coding the clean topics here — means removing or
        changing the filter in production SQL changes this fake's result.
        """
        m = re.search(r"topic in \(([^)]*)\)", q)
        return set(re.findall(r"'([^']*)'", m.group(1))) if m else None

    def _ledger_sum(self, pid, topics, before=None):
        return sum(
            r["delta_qty"]
            for r in self.commitment_ledger
            if r["product_id"] == pid
            and (topics is None or r["topic"] in topics)
            and (before is None or r["occurred_at"] < before)
        )

    # ---- asyncpg surface --------------------------------------------------
    async def fetch(self, query, *args):
        q = " ".join(query.split()).lower()

        if "from preorder.commitment_ledger cl" in q and "join preorder.product_status" in q:
            today, limit = args
            pids = {r["product_id"] for r in self.commitment_ledger}
            out = []
            for pid in pids:
                ps = self.product_status.get(pid)
                if (
                    ps
                    and ps["effective_pub_date"] is not None
                    and ps["effective_pub_date"] <= today
                    and ps["status"] in PREORDER_STATUSES
                    and pid not in self.lifecycle_snapshot
                ):
                    out.append({"product_id": pid, "effective_pub_date": ps["effective_pub_date"]})
            out.sort(key=lambda r: r["effective_pub_date"])
            return out[:limit]

        if "from preorder.lifecycle_snapshot" in q and "lifecycle_closed_at is null" in q:
            (limit,) = args
            return [
                {"product_id": pid}
                for pid, row in self.lifecycle_snapshot.items()
                if row["lifecycle_closed_at"] is None
            ][:limit]

        raise AssertionError(f"FakePool.fetch: unmodelled query: {q[:120]}")

    async def fetchrow(self, query, *args):
        q = " ".join(query.split()).lower()

        if q.startswith("select status from preorder.product_status"):
            ps = self.product_status.get(args[0])
            return {"status": ps["status"]} if ps else None

        if "from preorder.commitment_ledger" in q and "occurred_at < $2" in q:
            pid, cutoff = args
            return {"total": self._ledger_sum(pid, self._topics_in(q), before=cutoff)}

        if "from preorder.commitment_ledger" in q and "case when topic in" in q:
            (pid,) = args
            return {"total": self._ledger_sum(pid, self._topics_in(q))}

        if "from preorder.inventory_arrival" in q:
            at = self.inventory_arrival.get(args[0])
            return {"first_positive_inventory_at": at} if at else None

        raise AssertionError(f"FakePool.fetchrow: unmodelled query: {q[:120]}")

    async def execute(self, query, *args):
        q = " ".join(query.split()).lower()

        if q.startswith("insert into preorder.lifecycle_snapshot"):
            pid, eff, presale_total, first_arrival, committed, engine_version = args
            if pid not in self.lifecycle_snapshot:  # on conflict do nothing
                self.lifecycle_snapshot[pid] = {
                    "effective_pub_date": eff,
                    "presale_commitment_total": presale_total,
                    "first_inventory_arrival_at": first_arrival,
                    "current_committed_qty": committed,
                    "engine_version": engine_version,
                    "presale_fulfilled_at": None,
                    "lifecycle_closed_at": None,
                }
            return

        if q.startswith("update preorder.lifecycle_snapshot"):
            row = self.lifecycle_snapshot.get(args[0])
            if row and row["lifecycle_closed_at"] is None:
                now = datetime.now(UTC)
                row["presale_fulfilled_at"] = row["presale_fulfilled_at"] or now
                row["lifecycle_closed_at"] = now
            return

        raise AssertionError(f"FakePool.execute: unmodelled query: {q[:120]}")


@pytest.fixture
def pool(monkeypatch):
    p = FakePool()

    async def fake_get_pool():
        return p

    monkeypatch.setattr(ls, "get_pool", fake_get_pool)
    return p


def add_preorder(pool, pid, pub_date, status="historical_preorder"):
    pool.product_status[pid] = {"effective_pub_date": pub_date, "status": status}


def add_ledger(pool, pid, qty, at, topic="orders/create"):
    pool.commitment_ledger.append(
        {"product_id": pid, "delta_qty": qty, "occurred_at": at, "topic": topic}
    )


# ---- snapshot creation -----------------------------------------------------

async def test_snapshot_freezes_presale_cohort_before_et_midnight(pool):
    pid, pub = 1, date(2025, 10, 7)
    add_preorder(pool, pid, pub)
    cutoff = et_midnight_utc(pub)
    add_ledger(pool, pid, 5, cutoff - timedelta(days=1))                     # counts
    add_ledger(pool, pid, 3, cutoff + timedelta(hours=1))                    # post-pub
    add_ledger(pool, pid, 7, cutoff - timedelta(days=2), "orders/create_backfill")  # not a clean topic

    summary = await ls.run_daily()

    assert summary["created_snapshots"] == 1
    snap = pool.lifecycle_snapshot[pid]
    assert snap["presale_commitment_total"] == 5
    assert snap["effective_pub_date"] == pub
    assert snap["current_committed_qty"] == 8  # all clean topics, any time
    assert snap["engine_version"] == ls.ENGINE_VERSION


async def test_snapshot_is_idempotent(pool):
    pid, pub = 2, date(2025, 9, 1)
    add_preorder(pool, pid, pub)
    add_ledger(pool, pid, 1, et_midnight_utc(pub) - timedelta(days=1))

    first = await ls.run_daily()
    second = await ls.run_daily()

    assert first["created_snapshots"] == 1
    assert second["created_snapshots"] == 0
    assert len(pool.lifecycle_snapshot) == 1


async def test_no_ledger_activity_creates_no_snapshot(pool):
    add_preorder(pool, 3, date(2025, 8, 1))
    summary = await ls.run_daily()
    assert summary["created_snapshots"] == 0
    assert pool.lifecycle_snapshot == {}


async def test_non_preorder_status_is_not_snapshotted(pool):
    pid, pub = 5, date(2025, 8, 1)
    add_preorder(pool, pid, pub, status="not_a_preorder_product")
    add_ledger(pool, pid, 2, et_midnight_utc(pub) - timedelta(days=1))
    await ls.run_daily()
    assert pid not in pool.lifecycle_snapshot


async def test_future_pub_date_is_not_snapshotted(pool):
    pid, pub = 6, date.today() + timedelta(days=30)
    add_preorder(pool, pid, pub, status="active_preorder")
    add_ledger(pool, pid, 2, datetime.now(UTC))
    await ls.run_daily()
    assert pid not in pool.lifecycle_snapshot


# ---- closure ---------------------------------------------------------------

async def test_closure_requires_inventory_and_zero_commit(pool):
    pid, pub = 4, date(2025, 7, 1)
    add_preorder(pool, pid, pub)
    add_ledger(pool, pid, 5, et_midnight_utc(pub) - timedelta(days=1))
    pool.inventory_arrival[pid] = datetime.now(UTC)
    add_ledger(pool, pid, -5, datetime.now(UTC), "orders/fulfilled")

    summary = await ls.run_daily()

    assert summary["closed_snapshots"] == 1
    assert pool.lifecycle_snapshot[pid]["lifecycle_closed_at"] is not None


async def test_zero_commit_without_arrival_stays_open(pool):
    pid, pub = 7, date(2025, 7, 1)
    add_preorder(pool, pid, pub)
    add_ledger(pool, pid, 5, et_midnight_utc(pub) - timedelta(days=1))
    add_ledger(pool, pid, -5, datetime.now(UTC), "orders/fulfilled")

    await ls.run_daily()

    assert pool.lifecycle_snapshot[pid]["lifecycle_closed_at"] is None


async def test_arrival_with_open_commitments_stays_open(pool):
    pid, pub = 8, date(2025, 7, 1)
    add_preorder(pool, pid, pub)
    add_ledger(pool, pid, 5, et_midnight_utc(pub) - timedelta(days=1))
    pool.inventory_arrival[pid] = datetime.now(UTC)

    await ls.run_daily()

    assert pool.lifecycle_snapshot[pid]["lifecycle_closed_at"] is None
