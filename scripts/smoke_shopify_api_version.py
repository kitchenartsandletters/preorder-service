#!/usr/bin/env python3
"""
scripts/smoke_shopify_api_version.py — read-only, differential smoke test for a
Shopify Admin API version bump (rev2 Move 0.2).

What it does
------------
1. Extracts every GraphQL `query` operation production actually sends, straight
   from the source files listed in SOURCE_FILES (AST parse — no imports, so no
   job/env side effects, and no copied query text that can drift).
2. Runs each operation at a BASELINE version and a TARGET version.
3. Reports, per operation and version: OK / ERROR, the version Shopify actually
   served (`X-Shopify-API-Version` header), and any deprecation header.

It is READ-ONLY by construction: only operations whose text starts with `query`
are extracted, and any text containing `mutation` is refused. Mutations used by
this service (delivery profiles, productUpdate, collectionRemoveProducts,
publishablePublish/Unpublish, orderUpdate) were checked against the release
notes instead; they are not executed here.

Verdict
-------
- REGRESSION : OK at baseline, ERROR at target      -> blocks the bump
- PRE-EXISTING: ERROR at both                       -> not caused by the bump
- Also fails if Shopify did not serve the requested target version.
Exit code 0 = safe to bump; 1 = do not bump; 2 = setup problem.

Usage (needs production Shopify credentials; nothing is written)
-----
    # baseline = whatever this environment resolves today (e.g. Railway's var)
    railway run python scripts/smoke_shopify_api_version.py
    # explicit versions
    python scripts/smoke_shopify_api_version.py --baseline 2025-10 --target 2026-07

Env: SHOP_URL, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET (see shopify_token.py);
optional SHIPPING_REFERENCE_PROFILE_GID.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from shopify_version import DEFAULT_SHOPIFY_API_VERSION, get_api_version  # noqa: E402

# Files that send Admin GraphQL queries in production.
SOURCE_FILES = [
    "shopify_client.py",
    "shopify_service.py",
    "services/shipping_profiles.py",
    "services/shopify_cleanup.py",
    "services/weekly_release_engine.py",
    "routes/admin_preorders.py",
    "jobs/order_tagger.py",
    "jobs/nyt_reporter.py",
    "ledger_reconciliation.py",
    "populate_inventory_item_map.py",
]

# Query-bearing files deliberately NOT smoke-tested, with the reason.
# tests/test_smoke_shopify_api_version.py fails if any other file contains a
# GraphQL operation without being in SOURCE_FILES or here.
EXCLUDED_SOURCE_FILES = {
    "weekly_release_engine.py": "dead earlier phase, being retired (DOCS_STATUS Landmine 1)",
}

OPERATION_RE = re.compile(r"^\s*query\s+(\w+)\s*[({]")

DEFAULT_PRODUCT_ID = 7300376330373  # Saint Peter: Chapter & Verse (rev2 test title)

Key = Tuple[str, str]  # (source file, operation name)


# --------------------------------------------------------------------------
# Operation extraction (offline; unit-tested)
# --------------------------------------------------------------------------

def extract_operations(repo: Path = REPO) -> Dict[Key, str]:
    """Return {(file, operation_name): query_text} for every read query."""
    ops: Dict[Key, str] = {}
    for rel in SOURCE_FILES:
        tree = ast.parse((repo / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                m = OPERATION_RE.match(node.value)
                if not m:
                    continue
                if re.search(r"\bmutation\b", node.value):
                    raise RuntimeError(f"refusing operation containing 'mutation' in {rel}")
                ops[(rel, m.group(1))] = node.value
    return ops


# --------------------------------------------------------------------------
# Variables per operation. Values mirror what production passes, so the
# request exercises the same schema paths. A newly added production query has
# no entry here; tests/test_smoke_shopify_api_version.py fails until one is
# added, so smoke coverage cannot silently fall behind.
# --------------------------------------------------------------------------

def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


VARIABLE_FACTORIES: Dict[Key, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    ("shopify_client.py", "GetProduct"): lambda c: {"id": c["product_gid"]},
    ("shopify_service.py", "ProductFull"): lambda c: {
        "id": c["product_gid"], "collectionsFirst": 250, "variantsFirst": 250, "metafieldsFirst": 50,
    },
    ("shopify_service.py", "InventoryItemToProduct"): lambda c: {"id": c["inventory_item_gid"]},
    ("services/shipping_profiles.py", "ListDeliveryProfiles"): lambda c: {"cursor": None},
    ("services/shipping_profiles.py", "DeliveryProfileDetail"): lambda c: {"id": c["profile_gid"]},
    ("services/shipping_profiles.py", "ReferenceProfileStructure"): lambda c: {"id": c["reference_profile_gid"]},
    ("services/shipping_profiles.py", "ProductVariant"): lambda c: {"id": c["product_gid"]},
    ("services/shopify_cleanup.py", "ProductCleanupState"): lambda c: {"id": c["product_gid"]},
    ("services/weekly_release_engine.py", "Product"): lambda c: {"id": c["product_gid"]},
    ("services/weekly_release_engine.py", "Orders"): lambda c: {
        "cursor": None, "query": f"created_at:>={_since(7)}",
    },
    ("routes/admin_preorders.py", "Orders"): lambda c: {
        "cursor": None, "query": f"created_at:>={_since(7)} financial_status:paid",
    },
    ("jobs/order_tagger.py", "FetchOrders"): lambda c: {
        "query": f"created_at:>={_since(7)}", "first": 50, "after": None,
    },
    ("jobs/nyt_reporter.py", "WeekSales"): lambda c: {
        "q": f"created_at:>={_since(7)}", "first": 250, "after": None,
    },
    ("ledger_reconciliation.py", "OpenOrderCommitmentsForProduct"): lambda c: {
        "cursor": None, "query": f"line_items.product_id:{c['product_id']} status:open",
    },
    ("populate_inventory_item_map.py", "ProductsWithVariants"): lambda c: {"cursor": None},
}

# Informational (read-only): is the shop on market-driven shipping? If true,
# merchant-owned delivery-profile reads may be stale and writes may silently
# no-op (DOCS_STATUS Landmine 11). Run at the target version only; the field is
# not guaranteed to exist on older versions. Never affects the verdict.
MDS_QUERY = "query SmokeMarketDrivenShipping { shop { features { marketDrivenShipping } } }"

# Setup lookup (read-only) to find real IDs for the variables above.
SETUP_QUERY = """
query SmokeSetup($id: ID!) {
  product(id: $id) { id variants(first: 1) { nodes { inventoryItem { id } } } }
  deliveryProfiles(first: 5) { nodes { id default } }
}
"""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _post(client, domain: str, token: str, version: str, query: str, variables: Dict[str, Any]):
    url = f"https://{domain}/admin/api/{version}/graphql.json"
    for attempt in range(4):
        resp = client.post(
            url,
            json={"query": query, "variables": variables},
            headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        )
        body: Dict[str, Any] = {}
        try:
            body = resp.json()
        except ValueError:
            pass
        codes = {(e.get("extensions") or {}).get("code") for e in body.get("errors") or []}
        if resp.status_code == 429 or "THROTTLED" in codes:
            time.sleep(2 * (attempt + 1))
            continue
        return resp, body
    return resp, body


def _summarize(resp, body) -> Dict[str, Any]:
    errors = body.get("errors") or []
    return {
        "ok": resp.status_code == 200 and not errors and body.get("data") is not None,
        "status": resp.status_code,
        "served": resp.headers.get("X-Shopify-API-Version"),
        "deprecated": resp.headers.get("X-Shopify-API-Deprecated-Reason"),
        "errors": [
            f"{(e.get('extensions') or {}).get('code', '')} {e.get('message', '')}".strip()
            for e in errors
        ][:3],
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", default=get_api_version(),
                    help="version to compare against (default: this environment's resolved version)")
    ap.add_argument("--target", default=DEFAULT_SHOPIFY_API_VERSION,
                    help="version to validate (default: shopify_version.DEFAULT_SHOPIFY_API_VERSION)")
    ap.add_argument("--product-id", type=int, default=DEFAULT_PRODUCT_ID)
    args = ap.parse_args(argv)

    import httpx
    from shopify_token import get_token_sync, normalize_domain

    shop = os.getenv("SHOP_URL")
    if not shop:
        print("SHOP_URL is not set", file=sys.stderr)
        return 2
    domain = normalize_domain(shop)
    token = get_token_sync()
    ops = extract_operations()
    missing = sorted(set(ops) - set(VARIABLE_FACTORIES))
    if missing:
        print(f"No variables defined for: {missing}", file=sys.stderr)
        return 2

    versions = [args.baseline] if args.baseline == args.target else [args.baseline, args.target]
    print(f"shop={domain} product_id={args.product_id} baseline={args.baseline} target={args.target}")

    with httpx.Client(timeout=60) as client:
        resp, body = _post(client, domain, token, args.target, SETUP_QUERY,
                           {"id": f"gid://shopify/Product/{args.product_id}"})
        data = body.get("data") or {}
        product = data.get("product")
        profiles = (data.get("deliveryProfiles") or {}).get("nodes") or []
        variants = ((product or {}).get("variants") or {}).get("nodes") or []
        if not product or not variants or not profiles:
            print(f"Setup lookup failed at {args.target}: {_summarize(resp, body)}", file=sys.stderr)
            return 2
        default_profile = next((p["id"] for p in profiles if p.get("default")), profiles[0]["id"])
        ctx = {
            "product_id": args.product_id,
            "product_gid": product["id"],
            "inventory_item_gid": variants[0]["inventoryItem"]["id"],
            "profile_gid": default_profile,
            "reference_profile_gid": os.getenv("SHIPPING_REFERENCE_PROFILE_GID") or default_profile,
        }

        mds_resp, mds_body = _post(client, domain, token, args.target, MDS_QUERY, {})
        mds = (((mds_body.get("data") or {}).get("shop") or {}).get("features") or {}).get(
            "marketDrivenShipping"
        )
        mds_note = (
            f"marketDrivenShipping={mds}" if mds is not None
            else f"could not read ({_summarize(mds_resp, mds_body)['errors'] or 'no data'})"
        )

        results: Dict[Key, Dict[str, Dict[str, Any]]] = {}
        for key in sorted(ops):
            variables = VARIABLE_FACTORIES[key](ctx)
            results[key] = {
                v: _summarize(*_post(client, domain, token, v, ops[key], variables)) for v in versions
            }

    regressions, preexisting, served_mismatch, deprecated = [], [], [], []
    for key, per in results.items():
        label = f"{key[0]}::{key[1]}"
        for v, r in per.items():
            mark = "OK   " if r["ok"] else "ERROR"
            print(f"  [{v}] {mark} {label}  served={r['served']}"
                  + (f"  errors={r['errors']}" if r["errors"] else ""))
            if r["served"] and r["served"] != v:
                served_mismatch.append(f"{label}: requested {v}, served {r['served']}")
            if r["deprecated"]:
                deprecated.append(f"[{v}] {label}: {r['deprecated']}")
        tgt = per[args.target]
        base = per[args.baseline]
        if base["ok"] and not tgt["ok"]:
            regressions.append(label)
        elif not base["ok"] and not tgt["ok"]:
            preexisting.append(label)

    print()
    print(f"operations checked: {len(results)}  versions: {versions}")
    for title, items in (("REGRESSIONS (block the bump)", regressions),
                         ("PRE-EXISTING errors (fail on both; not caused by the bump)", preexisting),
                         ("SERVED-VERSION mismatches", served_mismatch),
                         ("DEPRECATION headers (informational)", deprecated)):
        print(f"{title}: {len(items)}")
        for item in items:
            print(f"  - {item}")

    print(f"MARKET-DRIVEN SHIPPING (informational, Landmine 11): {mds_note}")
    if mds is True:
        print("  WARNING: shop is on market-driven shipping; the week-based shipping-profile"
              " system's merchant-profile reads/writes may be stale or silent no-ops.")

    target_mismatch = [m for m in served_mismatch if f"requested {args.target}," in m]
    verdict_ok = not regressions and not target_mismatch
    print("\nVERDICT:", "SAFE TO BUMP" if verdict_ok else "DO NOT BUMP")
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
