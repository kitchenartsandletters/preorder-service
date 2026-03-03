import argparse
import asyncio
import json
from typing import List, Optional

from services.supabase_client import get_client as get_supabase_client
from shopify_client import ShopifyClient
from services.reclassification_service import reclassify_single_product
from audits.shopify_alignment_audit import compute_expected_state  # reuse engine-only function


async def fetch_product_ids_by_status(supabase, status: str, limit: Optional[int] = None) -> List[int]:
    query = (
        supabase
        .schema("preorder")
        .table("product_status")
        .select("product_id")
        .eq("status", status)
    )

    if limit:
        query = query.limit(limit)

    response = query.execute()
    return [row["product_id"] for row in response.data]


async def main():
    parser = argparse.ArgumentParser()

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", help="Reclassify all products with this persisted status")
    group.add_argument(
        "--product-ids",
        nargs="+",
        type=int,
        help="Explicit product IDs to reclassify",
    )

    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    supabase = get_supabase_client()
    shopify_client = ShopifyClient()

    if args.product_ids:
        product_ids = args.product_ids
        print(f"Found {len(product_ids)} explicit product_ids")
    else:
        product_ids = await fetch_product_ids_by_status(
            supabase,
            status=args.status,
            limit=args.limit,
        )
        print(f"Found {len(product_ids)} products with status='{args.status}'")

    drift_count = 0
    reclassified = 0

    for product_id in product_ids:
        try:
            # --- Compute expected (engine only) ---
            expected = await compute_expected_state(
                product_id=product_id,
                shopify_client=shopify_client,
            )

            # --- Fetch persisted ---
            persisted_resp = (
                supabase
                .schema("preorder")
                .table("product_status")
                .select("status, effective_pub_date")
                .eq("product_id", product_id)
                .limit(1)
                .execute()
            )

            if not persisted_resp.data:
                continue

            persisted = persisted_resp.data[0]

            if (
                expected["status"] != persisted["status"]
                or str(expected["effective_pub_date"]) != str(persisted["effective_pub_date"])
            ):
                drift_count += 1

                print(
                    f"[DRIFT] {product_id} "
                    f"{persisted['status']} -> {expected['status']}"
                )

                if not args.dry_run:
                    await reclassify_single_product(
                        supabase=supabase,
                        shopify_client=shopify_client,
                        product_id=product_id,
                    )
                    reclassified += 1

        except Exception as e:
            print(f"[ERROR] product_id={product_id} {e}")

    print("\n=== Bulk Reclassification Summary ===")
    print(json.dumps({
        "total_checked": len(product_ids),
        "drift_detected": drift_count,
        "reclassified": reclassified,
        "dry_run": args.dry_run,
    }, indent=2))

    await shopify_client.close()


if __name__ == "__main__":
    asyncio.run(main())