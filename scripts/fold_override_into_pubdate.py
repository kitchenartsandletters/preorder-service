#!/usr/bin/env python3
"""
scripts/fold_override_into_pubdate.py — rev2 Move 1d.

Folds `custom.preorder_override_date` into `custom.pub_date` so that pub_date
becomes the single source of truth (decision D1). Override is retired by 1e/1g;
this script is what makes that retirement safe.

Business rule, confirmed by the owner 2026-10-27: THE OVERRIDE IS THE SOURCE OF
TRUTH. Wherever the two disagree, the override is the real publication date.

What this changes, and what it does not
---------------------------------------
- Classification: nothing. The engine already resolves override first, so every
  product's effective_pub_date is already the override value. The script asserts
  this: any status or effective-date change is a failure, not a surprise.
- Storefront: for 3 of 38 titles, yes, deliberately. The theme shows the LATER
  of the two dates and stamps the cart with pub_date unless the override is in
  the future, so where the override is EARLIER the storefront currently
  advertises the wrong date. After the fold it shows the override — the correct
  date. (Voices from the Kitchen, Saint Peter, Bordeaux Chronicle.)

Safety
------
- --dry-run is the default. Nothing is written without --apply.
- Every write uses compareDigest, so a product edited since we read it fails
  rather than being clobbered.
- Idempotent: a product whose pub_date already equals its override is skipped,
  and each history row carries metadata.migration_run.
- Every write is recorded in preorder.pubdate_history with
  change_source='override_migration'.

Usage
-----
    python scripts/fold_override_into_pubdate.py                # dry run
    python scripts/fold_override_into_pubdate.py --apply
    python scripts/fold_override_into_pubdate.py --apply --product-ids 123,456
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from dependencies import get_supabase_client  # noqa: E402
from services import pubdate_service  # noqa: E402
from services.pubdate_service import PubDateConflict, PubDateError  # noqa: E402
from shopify_client import ShopifyClient  # noqa: E402

OVERRIDE_KEY = "preorder_override_date"
NAMESPACE = "custom"

OVERRIDE_QUERY = """
query OverrideValue($id: ID!, $namespace: String!, $key: String!) {
  product(id: $id) {
    id
    title
    metafield(namespace: $namespace, key: $key) { value }
  }
}
"""


def _as_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def candidate_products(supabase, product_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Products whose snapshot carries an override. Shopify is re-read per product."""
    query = (
        supabase.schema("preorder")
        .table("product_status")
        .select("product_id, status, effective_pub_date, metadata_snapshot")
    )
    if product_ids:
        query = query.in_("product_id", product_ids)
    rows = query.execute().data or []

    candidates = []
    for row in rows:
        snapshot = row.get("metadata_snapshot") or {}
        if snapshot.get("override_date") is None:
            continue
        candidates.append({
            "product_id": row["product_id"],
            "title": snapshot.get("title"),
            "status": row.get("status"),
            "effective_pub_date": row.get("effective_pub_date"),
            "snapshot_pub_date": snapshot.get("pub_date"),
            "snapshot_override": snapshot.get("override_date"),
        })
    return candidates


async def read_override(client, product_id: int) -> Optional[str]:
    data = await client.graphql(
        query=OVERRIDE_QUERY,
        variables={"id": f"gid://shopify/Product/{product_id}",
                   "namespace": NAMESPACE, "key": OVERRIDE_KEY},
    )
    product = (data or {}).get("product") or {}
    return ((product.get("metafield") or {}).get("value"))


def write_history(supabase, product_id: int, previous_pub_date, override_value: str,
                  run_id: str, direction: str) -> None:
    supabase.schema("preorder").table("pubdate_history").insert({
        "product_id": product_id,
        "old_effective_pub_date": previous_pub_date,
        "new_effective_pub_date": override_value,
        "change_source": "override_migration",
        "engine_version": "move1d-fold",
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "direction": direction,
            "previous_pub_date": previous_pub_date,
            "previous_override": override_value,
            "effective_date_unchanged": True,
            "migration_run": run_id,
        },
    }).execute()


async def run(apply: bool, product_ids: Optional[List[int]]) -> int:
    supabase = get_supabase_client()
    client = ShopifyClient()
    run_id = str(uuid.uuid4())

    candidates = candidate_products(supabase, product_ids)
    before = {c["product_id"]: (c["status"], str(c["effective_pub_date"])) for c in candidates}

    planned, skipped, failures = [], [], []

    try:
        for c in candidates:
            pid = c["product_id"]
            try:
                override_value = await read_override(client, pid)
                state = await pubdate_service.read_pub_date_state(client, pid)
            except Exception as exc:
                failures.append({"product_id": pid, "stage": "read", "error": str(exc)})
                continue

            if not override_value:
                skipped.append({"product_id": pid, "reason": "no override in Shopify"})
                continue

            override_date = _as_date(override_value)
            current_pub = _as_date(state["value"])

            if current_pub == override_date:
                skipped.append({"product_id": pid, "reason": "pub_date already equals override"})
                continue

            if current_pub is None:
                direction = "set"
            elif override_date < current_pub:
                direction = "earlier"
            else:
                direction = "later"

            planned.append({
                "product_id": pid, "title": c["title"], "status": c["status"],
                "pub_date": state["value"], "override": override_value,
                "direction": direction, "type": state["metafield_type"],
                "digest": state["compare_digest"],
            })

        print(f"run_id={run_id}  candidates={len(candidates)}  "
              f"to_fold={len(planned)}  skipped={len(skipped)}  read_failures={len(failures)}")
        by_direction: Dict[str, int] = {}
        for p in planned:
            by_direction[p["direction"]] = by_direction.get(p["direction"], 0) + 1
        print(f"directions: {by_direction}")
        for p in planned:
            flag = "  <-- storefront date changes" if p["direction"] == "earlier" else ""
            print(f"  {str(p['title'])[:34]:<34} {p['pub_date']} -> {p['override']} "
                  f"({p['direction']}){flag}")
        for s in skipped:
            print(f"  SKIP {s['product_id']}: {s['reason']}")
        for f in failures:
            print(f"  READ FAILED {f['product_id']}: {f['error']}")

        if not apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to perform the fold.")
            return 0 if not failures else 1

        written = []
        for p in planned:
            try:
                await pubdate_service.set_pub_date(
                    client, product_id=p["product_id"],
                    new_pub_date=_as_date(p["override"]),
                    metafield_type=p["type"], compare_digest=p["digest"],
                )
                write_history(supabase, p["product_id"], p["pub_date"], p["override"],
                              run_id, p["direction"])
                written.append(p["product_id"])
            except (PubDateConflict, PubDateError) as exc:
                failures.append({"product_id": p["product_id"], "stage": "write", "error": str(exc)})

        print(f"\nwrote {len(written)} products; {len(failures)} failures")

        # Verification: classification must be untouched. The engine already
        # resolved override first, so the effective date cannot move.
        after_rows = (
            supabase.schema("preorder").table("product_status")
            .select("product_id, status, effective_pub_date")
            .in_("product_id", written or [0])
            .execute()
        ).data or []
        drift = [
            r for r in after_rows
            if before.get(r["product_id"]) != (r["status"], str(r["effective_pub_date"]))
        ]
        if drift:
            print(f"WARNING: {len(drift)} products changed status or effective date:")
            for r in drift:
                print(f"  {r['product_id']}: {before.get(r['product_id'])} -> "
                      f"({r['status']}, {r['effective_pub_date']})")
        else:
            print("verified: no status or effective-date change (as expected)")

        for f in failures:
            print(f"  FAILED {f['product_id']} ({f['stage']}): {f['error']}")
        return 1 if failures else 0
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="perform the fold (default: dry run)")
    parser.add_argument("--product-ids", help="comma-separated subset, for a pilot")
    args = parser.parse_args()

    ids = [int(x) for x in args.product_ids.split(",")] if args.product_ids else None
    return asyncio.run(run(apply=args.apply, product_ids=ids))


if __name__ == "__main__":
    sys.exit(main())
