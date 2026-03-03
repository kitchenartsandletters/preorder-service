import argparse
import asyncio
import json
import sys
from datetime import date
from typing import Optional, List, Dict, Any

from shopify_client import ShopifyClient
from services.supabase_client import get_client as get_supabase_client
from shopify_service import build_product_metadata_from_shopify
from classification.engine import classify_preorder_product, ClassificationInput
from override_service import fetch_override_date

from dotenv import load_dotenv
load_dotenv()


# ----------------------------
# Utilities
# ----------------------------

def _parse_date(raw):
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        return date.fromisoformat(raw)
    return None


def _normalize_pubdate(value):
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


# ----------------------------
# Core Audit Logic
# ----------------------------

async def compute_expected_state(
    product_id: int,
    shopify_client,
    supabase=None,
) -> Dict[str, Any]:
    """
    Engine-only computation of expected classification.
    No persistence. Mirrors orchestrator input logic.
    """

    metadata = await build_product_metadata_from_shopify(
        product_id=product_id,
        client=shopify_client,
    )

    if not metadata:
        return {
            "status": None,
            "anomaly_type": None,
            "effective_pub_date": None,
        }

    override_from_db = None
    if supabase:
        override_from_db = fetch_override_date(
            supabase=supabase,
            product_id=product_id,
        )

    engine_input = ClassificationInput(
        product_id=metadata.product_id,
        tags=metadata.tags,
        in_preorder_collection=metadata.in_preorder_collection,
        date_tags=metadata.parsed_date_tags(),
        pub_date=_parse_date(metadata.pub_date_raw),
        override_date=_parse_date(
            override_from_db if override_from_db else metadata.override_date_raw
        ),
        inventory=metadata.inventory,
    )

    expected = classify_preorder_product(engine_input)

    return {
        "status": expected.status,
        "anomaly_type": expected.anomaly_type,
        "effective_pub_date": (
            expected.effective_pub_date.isoformat()
            if expected.effective_pub_date
            else None
        ),
    }

async def audit_product(
    product_id: int,
    supabase,
    shopify_client,
    verbose: bool = False,
) -> Dict[str, Any]:

    try:
        # 1. Fetch live Shopify metadata
        metadata = await build_product_metadata_from_shopify(
            product_id=product_id,
            client=shopify_client,
        )

        if not metadata:
            return {
                "product_id": product_id,
                "result": "error",
                "reason": "shopify_metadata_null"
            }

        # 2. Fetch DB override (authoritative)
        override_from_db = fetch_override_date(
            supabase=supabase,
            product_id=product_id,
        )

        # 3. Build ClassificationInput (mirror orchestrator)
        engine_input = ClassificationInput(
            product_id=metadata.product_id,
            tags=metadata.tags,
            in_preorder_collection=metadata.in_preorder_collection,
            date_tags=metadata.parsed_date_tags(),
            pub_date=_parse_date(metadata.pub_date_raw),
            override_date=_parse_date(
                override_from_db if override_from_db else metadata.override_date_raw
            ),
            inventory=metadata.inventory,
        )

        # 4. Compute expected classification
        expected = classify_preorder_product(engine_input)

        expected_pub = (
            expected.effective_pub_date.isoformat()
            if expected.effective_pub_date
            else None
        )

        # 5. Fetch persisted truth
        persisted_response = (
            supabase
            .schema("preorder")
            .table("product_status")
            .select("*")
            .eq("product_id", product_id)
            .execute()
        )

        rows = persisted_response.data if hasattr(persisted_response, "data") else None

        if not rows:
            return {
                "product_id": product_id,
                "result": "missing_persisted_row",
                "expected": {
                    "status": expected.status,
                    "anomaly_type": expected.anomaly_type,
                    "effective_pub_date": expected_pub,
                }
            }

        persisted = rows[0]

        persisted_pub = _normalize_pubdate(persisted.get("effective_pub_date"))

        drift = (
            expected.status != persisted.get("status")
            or expected.anomaly_type != persisted.get("anomaly_type")
            or expected_pub != persisted_pub
        )

        result_type = "drift" if drift else "match"

        if verbose:
            print(
                f"[{result_type.upper()}] product_id={product_id} "
                f"expected={expected.status}/{expected_pub} "
                f"persisted={persisted.get('status')}/{persisted_pub}"
            )

        return {
            "product_id": product_id,
            "result": result_type,
            "expected": {
                "status": expected.status,
                "anomaly_type": expected.anomaly_type,
                "effective_pub_date": expected_pub,
            },
            "persisted": {
                "status": persisted.get("status"),
                "anomaly_type": persisted.get("anomaly_type"),
                "effective_pub_date": persisted_pub,
                "last_classified_at": persisted.get("last_classified_at"),
            },
        }

    except Exception as e:
        return {
            "product_id": product_id,
            "result": "error",
            "reason": str(e),
        }


# ----------------------------
# Runner
# ----------------------------

async def run_audit(
    product_ids: List[int],
    fail_on_drift: bool = False,
    verbose: bool = False,
) -> int:

    supabase = get_supabase_client()
    shopify_client = ShopifyClient()

    results = []
    for pid in product_ids:
        item = await audit_product(pid, supabase, shopify_client, verbose=verbose)
        results.append(item)

    await shopify_client.close()

    summary = {
        "total": len(results),
        "match": sum(1 for r in results if r["result"] == "match"),
        "drift": sum(1 for r in results if r["result"] == "drift"),
        "missing": sum(1 for r in results if r["result"] == "missing_persisted_row"),
        "errors": sum(1 for r in results if r["result"] == "error"),
    }

    print("\n=== Shopify Alignment Audit Summary ===")
    print(json.dumps(summary, indent=2))

    if fail_on_drift and (summary["drift"] > 0 or summary["errors"] > 0):
        return 2

    return 0


# ----------------------------
# CLI Entry
# ----------------------------

def main():
    parser = argparse.ArgumentParser(description="Shopify Alignment Audit")

    parser.add_argument(
        "--product-ids",
        nargs="+",
        type=int,
        required=True,
        help="Explicit list of product IDs to audit",
    )

    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="Exit with code 2 if drift detected",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-product comparison",
    )

    args = parser.parse_args()

    exit_code = asyncio.run(
        run_audit(
            product_ids=args.product_ids,
            fail_on_drift=args.fail_on_drift,
            verbose=args.verbose,
        )
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()