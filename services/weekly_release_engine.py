#!/usr/bin/env python3
"""
Phase 13 — Weekly Release Engine

Deterministically derive the weekly preorder release dataset.

Reads only derived / authoritative preorder state:
- preorder.product_status
- preorder.lifecycle_snapshot
- preorder.inventory_arrival
- preorder.vw_arrival_timing
- preorder.vw_lifecycle_state

No mutations. No ledger writes. No classification writes.

Usage:
    python weekly_release_engine.py
    python weekly_release_engine.py --week 2026-04-07
    python weekly_release_engine.py --format json
    python weekly_release_engine.py --format csv
    python weekly_release_engine.py --output output/weekly_release_2026-04-07.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Literal, Optional
from zoneinfo import ZoneInfo

# Ensure project root is importable when running as a script.
# weekly_release_engine.py lives in /services, but the local modules
# (db/, services/, etc.) live at the repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.connection import get_pool  # noqa: E402


ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

ENGINE_VERSION = "v13-weekly-release-engine"


@dataclass(frozen=True)
class ReleaseWeekBounds:
    anchor_date: date
    week_start: date
    week_end: date


@dataclass(frozen=True)
class WeeklyReleaseRow:
    product_id: int
    effective_pub_date: date
    presale_commitment_total: int
    first_inventory_arrival_at: Optional[str]
    lifecycle_state: Optional[str]
    arrival_timing: Optional[str]
    classification_status: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deterministic weekly preorder release dataset."
    )
    parser.add_argument(
        "--week",
        type=str,
        default=None,
        help="Any date within the target release week, format YYYY-MM-DD. "
        "Default: current ET date.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "csv"),
        default="json",
        help="Output format.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional path to write output file. If omitted, prints to stdout.",
    )
    return parser.parse_args()


def today_et() -> date:
    return datetime.now(ET).date()


def resolve_anchor_date(raw: Optional[str]) -> date:
    if raw is None:
        return today_et()
    return date.fromisoformat(raw)


def get_release_week_bounds(anchor: date) -> ReleaseWeekBounds:
    # Monday-start week
    week_start = anchor - timedelta(days=anchor.weekday())
    week_end = week_start + timedelta(days=6)
    return ReleaseWeekBounds(
        anchor_date=anchor,
        week_start=week_start,
        week_end=week_end,
    )


async def fetch_release_candidates(
    pool,
    bounds: ReleaseWeekBounds,
) -> list[WeeklyReleaseRow]:
    """
    Weekly release dataset, derived only from frozen lifecycle + classification state.

    Inclusion:
    - status in ('active_preorder', 'historical_preorder')
    - effective_pub_date inside target week

    Exclusion:
    - anomaly_* statuses
    - not_a_preorder_product
    - missing effective_pub_date
    """
    rows = await pool.fetch(
        """
        select
            ps.product_id,
            ps.effective_pub_date,
            ls.presale_commitment_total,
            ls.first_inventory_arrival_at,
            vls.lifecycle_state,
            vat.arrival_timing,
            ps.status as classification_status
        from preorder.product_status ps
        join preorder.lifecycle_snapshot ls
          on ls.product_id = ps.product_id
        left join preorder.vw_lifecycle_state vls
          on vls.product_id = ps.product_id
        left join preorder.vw_arrival_timing vat
          on vat.product_id = ps.product_id
        where ps.status in ('active_preorder', 'historical_preorder')
          and ps.effective_pub_date is not null
          and ps.effective_pub_date >= $1::date
          and ps.effective_pub_date <= $2::date
        order by
            ps.effective_pub_date asc,
            ps.product_id asc
        """,
        bounds.week_start,
        bounds.week_end,
    )

    dataset: list[WeeklyReleaseRow] = []
    for r in rows:
        dataset.append(
            WeeklyReleaseRow(
                product_id=int(r["product_id"]),
                effective_pub_date=r["effective_pub_date"],
                presale_commitment_total=int(r["presale_commitment_total"] or 0),
                first_inventory_arrival_at=(
                    r["first_inventory_arrival_at"].isoformat()
                    if r["first_inventory_arrival_at"] is not None
                    else None
                ),
                lifecycle_state=r["lifecycle_state"],
                arrival_timing=r["arrival_timing"],
                classification_status=r["classification_status"],
            )
        )
    return dataset


def serialize_rows(rows: Iterable[WeeklyReleaseRow]) -> list[dict[str, Any]]:
    return [asdict(r) for r in rows]


def emit_json(rows: list[WeeklyReleaseRow], output: Optional[str]) -> None:
    payload = serialize_rows(rows)
    text = json.dumps(payload, indent=2, default=str)

    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def emit_csv(rows: list[WeeklyReleaseRow], output: Optional[str]) -> None:
    fieldnames = [
        "product_id",
        "effective_pub_date",
        "presale_commitment_total",
        "first_inventory_arrival_at",
        "lifecycle_state",
        "arrival_timing",
        "classification_status",
    ]

    if output:
        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in serialize_rows(rows):
                writer.writerow(row)
        return

    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    for row in serialize_rows(rows):
        writer.writerow(row)


async def build_release_dataset(
    anchor_date: Optional[date] = None,
) -> tuple[ReleaseWeekBounds, list[WeeklyReleaseRow]]:
    pool = await get_pool()
    bounds = get_release_week_bounds(anchor_date or today_et())
    rows = await fetch_release_candidates(pool, bounds)
    return bounds, rows


async def run(
    week: Optional[str],
    output_format: Literal["json", "csv"],
    output_path: Optional[str],
) -> None:
    anchor = resolve_anchor_date(week)
    bounds, rows = await build_release_dataset(anchor)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

    if output_format == "json":
        emit_json(rows, output_path)
    else:
        emit_csv(rows, output_path)

    if output_path:
        sys.stderr.write(
            f"[weekly_release_engine] wrote {len(rows)} rows "
            f"for week {bounds.week_start}..{bounds.week_end} "
            f"to {output_path}\n"
        )
    else:
        sys.stderr.write(
            f"[weekly_release_engine] emitted {len(rows)} rows "
            f"for week {bounds.week_start}..{bounds.week_end}\n"
        )


def main() -> None:
    args = parse_args()
    asyncio.run(
        run(
            week=args.week,
            output_format=args.format,
            output_path=args.output,
        )
    )


if __name__ == "__main__":
    main()