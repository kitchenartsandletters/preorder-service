import pytest
import asyncio
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import lifecycle_snapshotter as ls


ET = ZoneInfo("America/New_York")
UTC = timezone.utc


# --------------------------------------------------------
# Fake Async DB Pool
# --------------------------------------------------------

class FakePool:
    def __init__(self):
        self.product_status = {}
        self.lifecycle_snapshot = {}
        self.commitment_ledger = []
        self.inventory_arrival = {}

    async def fetch(self, query, *args):
        if "from preorder.product_status" in query:
            today = args[0]
            limit = args[1]
            results = []
            for pid, eff in self.product_status.items():
                if eff and eff <= today and pid not in self.lifecycle_snapshot:
                    results.append(
                        {"product_id": pid, "effective_pub_date": eff}
                    )
            return results[:limit]

        if "from preorder.lifecycle_snapshot" in query and "lifecycle_closed_at is null" in query:
            return [
                {"product_id": pid}
                for pid, row in self.lifecycle_snapshot.items()
                if row["lifecycle_closed_at"] is None
            ]

        return []

    async def fetchrow(self, query, *args):
        if "sum(delta_qty)" in query:
            pid = args[0]
            if "occurred_at <" in query:
                cutoff = args[1]
                total = sum(
                    r["delta_qty"]
                    for r in self.commitment_ledger
                    if r["product_id"] == pid
                    and r["occurred_at"] < cutoff
                )
                return {"total": total}
            else:
                total = sum(
                    r["delta_qty"]
                    for r in self.commitment_ledger
                    if r["product_id"] == pid
                )
                return {"total": total}

        if "from preorder.inventory_arrival" in query:
            pid = args[0]
            if pid in self.inventory_arrival:
                return {
                    "first_positive_inventory_at":
                        self.inventory_arrival[pid]
                }
            return None

        return None

    async def execute(self, query, *args):
        if "insert into preorder.lifecycle_snapshot" in query:
            pid = args[0]
            if pid not in self.lifecycle_snapshot:
                self.lifecycle_snapshot[pid] = {
                    "effective_pub_date": args[1],
                    "presale_commitment_total": args[2],
                    "presale_snapshot_at": datetime.now(UTC),
                    "engine_version": args[3],
                    "presale_fulfilled_at": None,
                    "lifecycle_closed_at": None,
                }

        if "update preorder.lifecycle_snapshot" in query:
            pid = args[0]
            if pid in self.lifecycle_snapshot:
                row = self.lifecycle_snapshot[pid]
                if row["lifecycle_closed_at"] is None:
                    now = datetime.now(UTC)
                    row["presale_fulfilled_at"] = now
                    row["lifecycle_closed_at"] = now


# --------------------------------------------------------
# Helpers
# --------------------------------------------------------

def et_midnight_utc(d: date):
    return datetime(d.year, d.month, d.day, 0, 0, tzinfo=ET).astimezone(UTC)


# --------------------------------------------------------
# Tests
# --------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_creation_freezes_cohort(monkeypatch):
    pool = FakePool()

    pub_date = date(2025, 10, 7)
    pid = 1

    pool.product_status[pid] = pub_date

    # presale before cutoff
    pool.commitment_ledger.append({
        "product_id": pid,
        "delta_qty": 5,
        "occurred_at": et_midnight_utc(pub_date) - timedelta(days=1),
    })

    # post-pub should not count
    pool.commitment_ledger.append({
        "product_id": pid,
        "delta_qty": 3,
        "occurred_at": et_midnight_utc(pub_date) + timedelta(hours=1),
    })

    monkeypatch.setattr(ls, "get_pool", lambda: asyncio.Future())
    f = asyncio.Future()
    f.set_result(pool)
    monkeypatch.setattr(ls, "get_pool", lambda: f)

    await ls.run_daily()

    snapshot = pool.lifecycle_snapshot[pid]
    assert snapshot["presale_commitment_total"] == 5


@pytest.mark.asyncio
async def test_idempotent_snapshot(monkeypatch):
    pool = FakePool()
    pid = 2
    pub_date = date(2025, 9, 1)

    pool.product_status[pid] = pub_date

    monkeypatch.setattr(ls, "get_pool", lambda: asyncio.Future())
    f = asyncio.Future()
    f.set_result(pool)
    monkeypatch.setattr(ls, "get_pool", lambda: f)

    await ls.run_daily()
    await ls.run_daily()

    assert len(pool.lifecycle_snapshot) == 1


@pytest.mark.asyncio
async def test_trivial_zero_presale_closes(monkeypatch):
    pool = FakePool()
    pid = 3
    pub_date = date(2025, 8, 1)

    pool.product_status[pid] = pub_date

    monkeypatch.setattr(ls, "get_pool", lambda: asyncio.Future())
    f = asyncio.Future()
    f.set_result(pool)
    monkeypatch.setattr(ls, "get_pool", lambda: f)

    await ls.run_daily()

    row = pool.lifecycle_snapshot[pid]
    assert row["lifecycle_closed_at"] is not None


@pytest.mark.asyncio
async def test_closure_requires_inventory_and_zero_commit(monkeypatch):
    pool = FakePool()
    pid = 4
    pub_date = date(2025, 7, 1)

    pool.product_status[pid] = pub_date

    # presale
    pool.commitment_ledger.append({
        "product_id": pid,
        "delta_qty": 5,
        "occurred_at": et_midnight_utc(pub_date) - timedelta(days=1),
    })

    # inventory arrival
    pool.inventory_arrival[pid] = datetime.now(UTC)

    # commit cleared
    pool.commitment_ledger.append({
        "product_id": pid,
        "delta_qty": -5,
        "occurred_at": datetime.now(UTC),
    })

    monkeypatch.setattr(ls, "get_pool", lambda: asyncio.Future())
    f = asyncio.Future()
    f.set_result(pool)
    monkeypatch.setattr(ls, "get_pool", lambda: f)

    await ls.run_daily()

    row = pool.lifecycle_snapshot[pid]
    assert row["lifecycle_closed_at"] is not None