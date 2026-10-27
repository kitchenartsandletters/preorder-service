#!/usr/bin/env python3
"""
scripts/delete_override_metafield.py — rev2 Move 1g.

Deletes `custom.preorder_override_date` from every product that still has it.

Run this AFTER Move 1e is deployed (the classifier no longer reads the override)
and after the fold (Move 1d, 2026-10-27) has set pub_date := override. By then
the metafield is inert: nothing in preorder-service reads it, and the storefront
branch that consulted it computes the same answer from pub_date alone.

Order matters, and it is the opposite of the intuitive one:
  1d fold  -> 1e stop reading  -> 1g delete
Deleting first would have stranded 37 products on a stale pub_date.

Safety
------
- --dry-run is the default; nothing is deleted without --apply.
- Deletes by metafield ID, read immediately beforehand, so a metafield created
  between read and write is never touched.
- Idempotent: a product without the metafield is skipped.
- Records nothing in pubdate_history: the effective date does not change, and
  the fold rows already document what each value was.

Usage
-----
    python scripts/delete_override_metafield.py
    python scripts/delete_override_metafield.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from dependencies import get_supabase_client  # noqa: E402
from shopify_client import ShopifyClient  # noqa: E402

NAMESPACE = "custom"
KEY = "preorder_override_date"
PAGE_SIZE = 1000
MAX_PAGES = 100

READ_QUERY = """
query OverrideMetafield($id: ID!, $namespace: String!, $key: String!) {
  product(id: $id) {
    id
    title
    metafield(namespace: $namespace, key: $key) { id value }
  }
}
"""

DELETE_MUTATION = """
mutation DeleteOverride($input: MetafieldDeleteInput!) {
  metafieldDelete(input: $input) {
    deletedId
    userErrors { field message }
  }
}
"""


def candidate_product_ids(supabase) -> List[int]:
    """
    Products whose last snapshot carried an override.

    Paged: PostgREST caps a response at 1000 rows and product_status holds
    ~15,400, so an unpaginated read silently returns the first page only.
    """
    ids: List[int] = []
    for page in range(MAX_PAGES):
        rows = (
            supabase.schema("preorder")
            .table("product_status")
            .select("product_id, metadata_snapshot")
            .order("product_id")
            .range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE - 1)
            .execute()
        ).data or []
        for row in rows:
            snapshot = row.get("metadata_snapshot") or {}
            if snapshot.get("override_date") is not None:
                ids.append(row["product_id"])
        if len(rows) < PAGE_SIZE:
            return ids
    raise RuntimeError("product_status exceeded the page cap; refusing a partial read")


async def run(apply: bool) -> int:
    supabase = get_supabase_client()
    client = ShopifyClient()
    deleted, skipped, failures = [], [], []

    try:
        product_ids = candidate_product_ids(supabase)
        print(f"candidates from product_status snapshots: {len(product_ids)}")

        for pid in product_ids:
            try:
                data = await client.graphql(
                    query=READ_QUERY,
                    variables={"id": f"gid://shopify/Product/{pid}",
                               "namespace": NAMESPACE, "key": KEY},
                )
            except Exception as exc:
                failures.append({"product_id": pid, "stage": "read", "error": str(exc)})
                continue

            product = (data or {}).get("product") or {}
            metafield = product.get("metafield") or {}
            if not metafield.get("id"):
                skipped.append(pid)
                continue

            title = (product.get("title") or "")[:34]
            if not apply:
                print(f"  would delete  {title:<34} value={metafield.get('value')}")
                deleted.append(pid)
                continue

            try:
                result = await client.graphql(
                    query=DELETE_MUTATION,
                    variables={"input": {"id": metafield["id"]}},
                )
                payload = (result or {}).get("metafieldDelete") or {}
                errors = payload.get("userErrors") or []
                if errors:
                    failures.append({"product_id": pid, "stage": "delete",
                                     "error": "; ".join(e.get("message", "") for e in errors)})
                    continue
                deleted.append(pid)
                print(f"  deleted       {title}")
            except Exception as exc:
                failures.append({"product_id": pid, "stage": "delete", "error": str(exc)})

        verb = "would delete" if not apply else "deleted"
        print(f"\n{verb}: {len(deleted)}   already absent: {len(skipped)}   failures: {len(failures)}")
        for f in failures:
            print(f"  FAILED {f['product_id']} ({f['stage']}): {f['error']}")
        if not apply:
            print("\nDRY RUN — nothing deleted. Re-run with --apply.")
        return 1 if failures else 0
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    args = parser.parse_args()
    return asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())
