"""
PROCESSOR: lifecycle_snapshotter.py

Phase 13 processor (Hybrid Model).

Hybrid architecture:
- Lifecycle state itself is DERIVED (via SQL view: preorder.vw_lifecycle_state).
- This processor FREEZES presale cohort data and stores diagnostic state snapshots.

Goals:
- Create ONE preorder.lifecycle_snapshot row per product once the product crosses the pub-date boundary.
- Freeze the "presale cohort" as the net commitment deltas that occurred strictly before ET midnight of effective_pub_date.
- Persist diagnostic fields required for lifecycle derivation observability.
- Mark presale_fulfilled_at + lifecycle_closed_at once the frozen cohort is considered satisfied.

Consumes (read-only):
- preorder.product_status (effective_pub_date)
- preorder.commitment_ledger (deltas)
- preorder.inventory_arrival (first_positive_inventory_at)

Writes:
- preorder.lifecycle_snapshot

Notes:
- Lifecycle state classification is NOT persisted here.
- Snapshot table provides frozen cohort + diagnostic facts.
- Time boundary for the presale cohort is ET midnight at effective_pub_date.
- Closure logic is a Phase 13 proxy and may be refined later when fulfillment-level signals are added.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from db.connection import get_pool

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

ENGINE_VERSION = "v13-lifecycle-snapshotter"


def et_midnight_to_utc(eff_pub_date: date) -> datetime:
    """Convert ET midnight at effective_pub_date to UTC timestamptz."""
    et_midnight = datetime(
        eff_pub_date.year,
        eff_pub_date.month,
        eff_pub_date.day,
        0,
        0,
        0,
        tzinfo=ET,
    )
    return et_midnight.astimezone(UTC)


def today_et_date() -> date:
    return datetime.now(ET).date()


@dataclass(frozen=True)
class SnapshotCandidate:
    product_id: int
    effective_pub_date: date


# -------------------------
# DB Queries
# -------------------------


async def fetch_snapshot_candidates(pool, limit: int = 5000) -> list[SnapshotCandidate]:
    """
    Ledger-driven candidate discovery.

    Products eligible for snapshot creation are derived from
    commitment_ledger activity rather than product_status alone.

    This guarantees the snapshotter processes every product that
    has ever had preorder commitment activity.
    """
    rows = await pool.fetch(
        """
        select distinct cl.product_id, ps.effective_pub_date
        from preorder.commitment_ledger cl
        join preorder.product_status ps
          on ps.product_id = cl.product_id
        left join preorder.lifecycle_snapshot ls
          on ls.product_id = cl.product_id
        where ps.effective_pub_date is not null
        and ps.effective_pub_date <= $1::date
        and ps.status in ('active_preorder', 'historical_preorder')
        and ls.product_id is null
        order by ps.effective_pub_date asc
        limit $2
        """,
        today_et_date(),
        limit,
    )
    return [SnapshotCandidate(int(r["product_id"]), r["effective_pub_date"]) for r in rows]


async def compute_presale_commitment_total(pool, product_id: int, eff_pub_date: date) -> int:
    """Frozen cohort: net commitment deltas strictly before ET midnight of effective_pub_date."""
    cutoff_utc = et_midnight_to_utc(eff_pub_date)
    row = await pool.fetchrow(
        """
        select coalesce(sum(delta_qty), 0) as total
        from preorder.commitment_ledger
        where product_id = $1
          and occurred_at < $2
          and topic in ('orders/create', 'orders/fulfilled', 'refunds/create')
        """,
        product_id,
        cutoff_utc,
    )
    return int(row["total"] if row else 0)


async def gather_snapshot_diagnostics(pool, product_id: int) -> tuple[datetime | None, int]:
    """
    Gather diagnostic facts for hybrid lifecycle derivation.

    Returns:
        (first_inventory_arrival_at, current_committed_qty)
    """
    first_positive = await get_first_positive_inventory_at(pool, product_id)
    committed = await get_current_preorder_committed_qty(pool, product_id)
    return first_positive, committed


async def insert_snapshot(
    pool,
    product_id: int,
    eff_pub_date: date,
    presale_total: int,
    first_inventory_arrival_at: datetime | None,
    current_committed_qty: int,
    engine_version: str,
) -> None:
    """Idempotent insert. One row per product."""
    await pool.execute(
        """
        insert into preorder.lifecycle_snapshot (
          product_id,
          effective_pub_date,
          presale_commitment_total,
          presale_snapshot_at,
          first_inventory_arrival_at,
          current_committed_qty,
          engine_version
        )
        values (
          $1,
          $2::date,
          $3,
          now(),
          $4,
          $5,
          $6
        )
        on conflict (product_id) do nothing
        """,
        product_id,
        eff_pub_date,
        presale_total,
        first_inventory_arrival_at,
        current_committed_qty,
        engine_version,
    )


async def fetch_open_snapshots(pool, limit: int = 5000) -> list[int]:
    rows = await pool.fetch(
        """
        select product_id
        from preorder.lifecycle_snapshot
        where lifecycle_closed_at is null
        limit $1
        """,
        limit,
    )
    return [int(r["product_id"]) for r in rows]


async def get_first_positive_inventory_at(pool, product_id: int) -> datetime | None:
    row = await pool.fetchrow(
        """
        select first_positive_inventory_at
        from preorder.inventory_arrival
        where product_id = $1
        """,
        product_id,
    )
    return row["first_positive_inventory_at"] if row else None


async def get_current_preorder_committed_qty(pool, product_id: int) -> int:
    """Current net commitment using ONLY clean ledger topics (post-reconciliation)."""
    row = await pool.fetchrow(
        """
        select coalesce(
        sum(
            case
            when topic in (
                'orders/create',
                'orders/fulfilled',
                'refunds/create'
            )
            then delta_qty
            else 0
            end
        ), 0
        ) as total
        from preorder.commitment_ledger
        where product_id = $1
        """,
        product_id,
    )
    return int(row["total"] if row else 0)


async def mark_closed(pool, product_id: int) -> None:
    await pool.execute(
        """
        update preorder.lifecycle_snapshot
        set presale_fulfilled_at = coalesce(presale_fulfilled_at, now()),
            lifecycle_closed_at = coalesce(lifecycle_closed_at, now())
        where product_id = $1
          and lifecycle_closed_at is null
        """,
        product_id,
    )


# -------------------------
# Lifecycle Logic
# -------------------------


async def presale_is_fulfilled_phase13_proxy(pool, product_id: int) -> bool:
    """ 
    Phase 13 proxy closure rule:

    - We require that some inventory has ever arrived (first_positive_inventory_at exists)
    - And that the current preorder committed quantity is 0

    This intentionally ignores post-pub ordering nuances; refinement comes later.
    """
    first_positive = await get_first_positive_inventory_at(pool, product_id)
    if not first_positive:
        return False

    committed = await get_current_preorder_committed_qty(pool, product_id)
    return committed <= 0


# -------------------------
# Entrypoint
# -------------------------


async def truncate_snapshots(pool) -> None:
    """
    Rebuild helper: remove all lifecycle snapshots so they can be deterministically
    recomputed from ledger + metadata.
    """
    await pool.execute("""
        truncate table preorder.lifecycle_snapshot
    """)


async def run_rebuild(limit: int = 50000) -> dict:
    """
    Deterministic rebuild mode.

    Steps:
    1. Truncate lifecycle_snapshot
    2. Recompute snapshots from ledger-derived candidates
    """
    pool = await get_pool()

    logger.info("[lifecycle_snapshotter] starting rebuild mode")

    await truncate_snapshots(pool)

    result = await run_daily(limit=limit)

    logger.info("[lifecycle_snapshotter] rebuild complete", extra=result)

    return result


async def run_daily(limit: int = 5000) -> dict:
    """Run snapshot creation + closure marking."""
    pool = await get_pool()

    created = 0
    closed = 0

    # 1) snapshot creation
    candidates = await fetch_snapshot_candidates(pool, limit=limit)
    for c in candidates:
        # SAFETY GUARD: ensure product is still a preorder at snapshot time
        status_row = await pool.fetchrow(
            """
            select status
            from preorder.product_status
            where product_id = $1
            """,
            c.product_id,
        )

        if not status_row or status_row["status"] not in ("active_preorder", "historical_preorder"):
            continue

        presale_total = await compute_presale_commitment_total(
            pool, c.product_id, c.effective_pub_date
        )

        first_inventory_arrival_at, current_committed_qty = await gather_snapshot_diagnostics(
            pool, c.product_id
        )

        await insert_snapshot(
            pool,
            c.product_id,
            c.effective_pub_date,
            presale_total,
            first_inventory_arrival_at,
            current_committed_qty,
            engine_version=ENGINE_VERSION,
        )
        created += 1

    # 2) closure marking
    open_pids = await fetch_open_snapshots(pool, limit=limit)
    for pid in open_pids:
        if await presale_is_fulfilled_phase13_proxy(pool, pid):
            await mark_closed(pool, pid)
            closed += 1

    summary = {
        "created_snapshots": created,
        "closed_snapshots": closed,
        "limit": limit,
        "engine_version": ENGINE_VERSION,
        "ran_at_utc": datetime.now(UTC).isoformat(),
    }

    logger.info("[lifecycle_snapshotter] completed", extra=summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    if "--rebuild" in sys.argv:
        asyncio.run(run_rebuild())
    else:
        asyncio.run(run_daily())


if __name__ == "__main__":
    main()