"""
jobs/run.py

Standardized cron runner for hybrid architecture.

Supports:
- commitment_ledger
- lifecycle_snapshotter

Design goals:
- Single entrypoint for Railway cron services
- Structured JSON summary output
- Proper exit codes (0 success, nonzero failure)
- Direct internal function calls (no subprocesses)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict
from pathlib import Path

# Ensure project root is on path BEFORE internal imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Internal job imports
from build_commitment_ledger import run as run_commitment_ledger
from lifecycle_snapshotter import run_daily as run_lifecycle_snapshotter

UTC = timezone.utc

def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


async def run_commitment_job(args) -> Dict[str, Any]:
    summary = await run_commitment_ledger(
        topic=args.topic,
        limit=args.limit,
        since=args.since,
        dry_run=args.dry_run,
    )
    return summary


async def run_lifecycle_job(args) -> Dict[str, Any]:
    summary = await run_lifecycle_snapshotter(
        limit=args.limit,
    )
    return summary


async def dispatch(args) -> Dict[str, Any]:
    if args.job == "commitment_ledger":
        return await run_commitment_job(args)

    if args.job == "lifecycle_snapshotter":
        return await run_lifecycle_job(args)

    raise ValueError(f"Unknown job: {args.job}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--job",
        required=True,
        choices=["commitment_ledger", "lifecycle_snapshotter"],
        help="Job to run",
    )

    # Shared
    parser.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="Batch size / limit (job-specific meaning)",
    )

    # Commitment ledger specific
    parser.add_argument(
        "--topic",
        default="all",
        help="orders/create | orders/fulfilled | orders/cancelled | refunds/create | all",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO timestamp lower bound for tracking.created_at",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write inserts (commitment_ledger only)",
    )

    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    parser = build_parser()
    args = parser.parse_args()

    started_at = now_utc_iso()

    try:
        summary = asyncio.run(dispatch(args))

        result = {
            "job": args.job,
            "ok": True,
            "started_at": started_at,
            "finished_at": now_utc_iso(),
            "summary": summary,
        }

        print(json.dumps(result, indent=2, default=str))
        sys.exit(0)

    except Exception as e:
        error_result = {
            "job": args.job,
            "ok": False,
            "started_at": started_at,
            "finished_at": now_utc_iso(),
            "error": str(e),
        }

        print(json.dumps(error_result, indent=2, default=str))
        sys.exit(1)


if __name__ == "__main__":
    main()