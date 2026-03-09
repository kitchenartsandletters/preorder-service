#!/usr/bin/env python3

import asyncio
import sys
from pathlib import Path

# Ensure project root is on PYTHONPATH so we import the local db module
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.connection import get_pool

EXPECTED_TABLES = {
    "tracking",
    "approvals",
    "product_status",
    "inventory_arrival",
    "lifecycle_snapshot",
    "pubdate_history",
    "commitment_ledger",
    "inventory_item_map",
    "product_overrides",
}


async def verify():
    pool = await get_pool()

    rows = await pool.fetch(
        """
        select table_name
        from information_schema.tables
        where table_schema = 'preorder'
        """
    )

    existing = {r["table_name"] for r in rows}

    missing = EXPECTED_TABLES - existing
    extra = existing - EXPECTED_TABLES

    print("\nPreorder Schema Validation\n")

    if missing:
        print("❌ Missing tables:")
        for t in sorted(missing):
            print(" -", t)
    else:
        print("✅ All required tables present")

    if extra:
        print("\n⚠️ Extra tables present:")
        for t in sorted(extra):
            print(" -", t)

    if not missing:
        print("\nSchema contract satisfied")


if __name__ == "__main__":
    asyncio.run(verify())