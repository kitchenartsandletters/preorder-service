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
import traceback
from typing import Any, Dict
from pathlib import Path

# Ensure project root is on path BEFORE internal imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Internal job imports
from build_commitment_ledger import run as run_commitment_ledger
from lifecycle_snapshotter import run_daily as run_lifecycle_snapshotter
from ledger_reconciliation import run as run_ledger_reconciliation
from jobs.pub_date_transition import run as run_pub_date_transition

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


async def run_reconciliation_job(args) -> Dict[str, Any]:
    summary = await run_ledger_reconciliation(
        limit=args.limit,
        write=not args.dry_run,
    )
    return summary

async def run_pub_date_transition_job(args) -> Dict[str, Any]:
    return await run_pub_date_transition(
        limit=args.limit,
        dry_run=args.dry_run,
    )


async def dispatch(args) -> Dict[str, Any]:
    if args.job == "commitment_ledger":
        return await run_commitment_job(args)

    if args.job == "lifecycle_snapshotter":
        return await run_lifecycle_job(args)

    if args.job == "ledger_reconciliation":
        return await run_reconciliation_job(args)
    
    if args.job == "pub_date_transition":
        return await run_pub_date_transition_job(args)

    raise ValueError(f"Unknown job: {args.job}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--job",
        required=True,
        choices=["commitment_ledger", "lifecycle_snapshotter", "ledger_reconciliation", "pub_date_transition"],
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
        help="Do not write inserts (commitment_ledger / reconciliation)",
    )

    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    parser = build_parser()
    args = parser.parse_args()

    started_at = now_utc_iso()

    logging.info("Starting cron job", extra={
        "job": args.job,
        "topic": getattr(args, "topic", None),
        "limit": args.limit,
        "since": getattr(args, "since", None),
        "dry_run": getattr(args, "dry_run", False),
    })

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
        tb = traceback.format_exc()

        logging.error("Cron job failed")
        logging.error(tb)

        error_result = {
            "job": args.job,
            "ok": False,
            "started_at": started_at,
            "finished_at": now_utc_iso(),
            "error": str(e),
            "traceback": tb,
        }

        print(json.dumps(error_result, indent=2, default=str))
        sys.exit(1)


if __name__ == "__main__":
    main()