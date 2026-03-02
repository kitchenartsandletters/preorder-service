#!/usr/bin/env python3
"""
Phase 13.5b — Build preorder.inventory_arrival

Arrival derived strictly from tracking topic: inventory_levels/update

Definition:
- For each (inventory_item_id, location_id), compute delta in `available` vs previous observed.
- If delta > 0, that's a positive inventory receipt event for that inventory_item_id.
- Map inventory_item_id -> product_id via preorder.inventory_item_map
- For product_id: write first_positive_inventory_at once (immutable).

Usage:
  python build_inventory_arrival.py
  python build_inventory_arrival.py --dry-run
  python build_inventory_arrival.py --since 2026-01-01T00:00:00Z
  python build_inventory_arrival.py --limit 5000
"""

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, List

from dateutil.parser import isoparse

from db.connection import get_pool

ENGINE_VERSION = "v13.5-inventory-arrival"
TOPIC = "inventory_levels/update"


def _as_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            dt = isoparse(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None


@dataclass(frozen=True)
class TrackingRow:
    id: str
    created_at: datetime
    payload: Dict[str, Any]


FETCH_BATCH_SQL = """
select id, created_at, payload
from preorder.tracking
where topic = $1
  and ($2::timestamptz is null or created_at >= $2::timestamptz)
  and (
    ($3::timestamptz is null and $4::uuid is null)
    or (created_at, id) > ($3::timestamptz, $4::uuid)
  )
order by created_at asc, id asc
limit $5
"""

MAP_LOOKUP_SQL = """
select product_id
from preorder.inventory_item_map
where inventory_item_id = $1
"""

INSERT_ARRIVAL_SQL = """
insert into preorder.inventory_arrival (product_id, first_positive_inventory_at, engine_version)
values ($1::bigint, $2::timestamptz, $3::text)
on conflict (product_id) do nothing
"""


def parse_payload(payload_raw: Any) -> Dict[str, Any]:
    if isinstance(payload_raw, dict):
        return payload_raw
    if isinstance(payload_raw, str):
        try:
            obj = json.loads(payload_raw)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return {}
    return {}


def extract_inventory_event(payload: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[datetime]]:
    """
    Returns: (inventory_item_id, location_id, available, occurred_at)
    """
    inventory_item_id = _as_int(payload.get("inventory_item_id"))
    location_id = _as_int(payload.get("location_id"))
    available = _as_int(payload.get("available"))
    occurred_at = _parse_ts(payload.get("updated_at")) or _parse_ts(payload.get("updatedAt"))
    return inventory_item_id, location_id, available, occurred_at


async def resolve_product_id(pool, inventory_item_id: int) -> Optional[int]:
    row = await pool.fetchrow(MAP_LOOKUP_SQL, inventory_item_id)
    if not row:
        return None
    return int(row["product_id"])


async def run(limit: int = 5000, since: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    pool = await get_pool()
    since_dt = _parse_ts(since) if since else None

    last_created_at: Optional[datetime] = None
    last_id: Optional[str] = None

    # per (inventory_item_id, location_id) last seen available
    last_available: Dict[Tuple[int, int], int] = {}

    scanned = 0
    deltas_positive = 0
    arrivals_insert_attempts = 0
    mapped_events = 0
    unmapped_events = 0

    while True:
        batch = await pool.fetch(FETCH_BATCH_SQL, TOPIC, since_dt, last_created_at, last_id, limit)
        if not batch:
            break

        scanned += len(batch)

        for r in batch:
            tracking_id = str(r["id"])
            created_at = r["created_at"]
            payload = parse_payload(r["payload"])

            inv_item_id, location_id, available, occurred_at = extract_inventory_event(payload)

            # advance keyset cursor
            last_created_at = created_at
            last_id = tracking_id

            if inv_item_id is None or location_id is None or available is None:
                continue

            if occurred_at is None:
                # if payload missing updated_at, use tracking.created_at
                occurred_at = created_at
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=timezone.utc)
                else:
                    occurred_at = occurred_at.astimezone(timezone.utc)

            key = (inv_item_id, location_id)
            if key not in last_available:
                # first time seeing this key → we cannot derive a delta yet
                last_available[key] = available
                continue

            delta = available - last_available[key]
            last_available[key] = available

            if delta <= 0:
                continue

            deltas_positive += 1

            product_id = await resolve_product_id(pool, inv_item_id)
            if product_id is None:
                unmapped_events += 1
                continue

            mapped_events += 1
            arrivals_insert_attempts += 1

            if not dry_run:
                await pool.execute(INSERT_ARRIVAL_SQL, product_id, occurred_at, ENGINE_VERSION)

    return {
        "topic": TOPIC,
        "engine_version": ENGINE_VERSION,
        "dry_run": dry_run,
        "since": since_dt.isoformat() if since_dt else None,
        "batch_limit": limit,
        "tracking_rows_scanned": scanned,
        "positive_deltas_detected": deltas_positive,
        "mapped_positive_deltas": mapped_events,
        "unmapped_positive_deltas": unmapped_events,
        "arrival_insert_attempts": arrivals_insert_attempts,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--since", default=None, help="ISO timestamp lower bound for tracking.created_at")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    summary = asyncio.run(run(limit=args.limit, since=args.since, dry_run=args.dry_run))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()