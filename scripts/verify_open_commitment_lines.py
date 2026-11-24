#!/usr/bin/env python3
"""
scripts/verify_open_commitment_lines.py

Read-only. Checks the commitment ledger's OPEN lines for one product against
Shopify's own order state, so fulfilment works from a verified count rather
than from ledger arithmetic.

WHY THIS EXISTS
---------------
The ledger's net open quantity is not trustworthy on its own:

- Webhook delivery to preorder-service is lossy (DOCS_STATUS Landmine 13:
  22,890 events lost 2026-04-27 to 2026-09-11, none replayed). A missed
  `orders/cancelled` or `refunds/create` leaves a line open forever.
- The nightly reconciliation sweep, which existed to catch exactly that, has
  not run since 2026-03-31 (Landmine 21).

So a line the ledger calls open may have been cancelled, refunded or shipped
months ago. This script asks Shopify.

WHAT IT DOES
------------
1. Pages `preorder.commitment_ledger` for the product and computes, per
   (order_id, line_item_id), the net of the clean topics
   `orders/create`, `orders/fulfilled`, `refunds/create` — the same topic set
   `lifecycle_snapshotter.get_current_preorder_committed_qty` uses.
2. For every line with net > 0, reads that order from the Shopify Admin API.
3. Classifies each line and prints a reconciliation.

VERDICTS
--------
  open              Shopify agrees the line is unfulfilled and not refunded.
                    These are real customers waiting.
  cancelled         The order is cancelled in Shopify. Ledger is stale.
  fulfilled         Shopify shows nothing unfulfilled. Ledger is stale.
  refunded          The line's current quantity is 0 (refunded/removed).
  line_missing      The line item is not on the order any more.
  order_missing     Shopify returned no such order.

Only `open` should be fulfilled. Everything else is ledger residue and wants a
repair pass of its own — this script writes nothing.

USAGE
-----
    export SHOP_URL=... SHOPIFY_CLIENT_ID=... SHOPIFY_CLIENT_SECRET=...
    export SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=...

    python scripts/verify_open_commitment_lines.py --product-id 6598890029189
    python scripts/verify_open_commitment_lines.py --product-id 6598890029189 \
        --json /tmp/septime_open_lines.json

Exit codes: 0 verified, 2 configuration or lookup failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# The topic set that lifecycle_snapshotter treats as authoritative commitment.
CLEAN_TOPICS = ("orders/create", "orders/fulfilled", "refunds/create")

PAGE_SIZE = 1000
MAX_PAGES = 100

ORDER_QUERY = """
query VerifyOpenLinesOrder($id: ID!) {
  order(id: $id) {
    id
    name
    createdAt
    cancelledAt
    displayFulfillmentStatus
    displayFinancialStatus
    lineItems(first: 100) {
      nodes {
        id
        quantity
        currentQuantity
        unfulfilledQuantity
        refundableQuantity
      }
    }
  }
}
"""


# --------------------------------------------------------------------------
# Ledger side (offline-testable)
# --------------------------------------------------------------------------

@dataclass
class OpenLine:
    order_id: int
    line_item_id: int
    net: int
    first_seen: Optional[str] = None
    verdict: str = "unchecked"
    detail: Dict[str, Any] = field(default_factory=dict)


def fetch_ledger_rows(supabase, product_id: int) -> List[Dict[str, Any]]:
    """Page every clean-topic ledger row for the product.

    Paged because PostgREST caps an unpaged select at 1000 rows, which has
    silently truncated a migration in this repo before (Move 1d).
    """
    rows: List[Dict[str, Any]] = []
    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        resp = (
            supabase.schema("preorder")
            .table("commitment_ledger")
            .select("order_id,line_item_id,topic,delta_qty,occurred_at")
            .eq("product_id", product_id)
            .in_("topic", list(CLEAN_TOPICS))
            .order("occurred_at")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
    raise RuntimeError(
        f"more than {MAX_PAGES * PAGE_SIZE} ledger rows for product {product_id}; raise MAX_PAGES"
    )


def compute_open_lines(rows: Iterable[Dict[str, Any]]) -> List[OpenLine]:
    """Net the clean topics per (order_id, line_item_id) and keep net > 0."""
    nets: Dict[Tuple[int, int], int] = defaultdict(int)
    first: Dict[Tuple[int, int], Optional[str]] = {}

    for row in rows:
        order_id = row.get("order_id")
        line_item_id = row.get("line_item_id")
        if order_id is None or line_item_id is None:
            # Adjustment and backfill rows carry no order identity; they are
            # not part of a per-line reconciliation.
            continue
        key = (int(order_id), int(line_item_id))
        nets[key] += int(row.get("delta_qty") or 0)
        occurred = row.get("occurred_at")
        if occurred is not None and (key not in first or str(occurred) < str(first[key])):
            first[key] = str(occurred)

    open_lines = [
        OpenLine(order_id=k[0], line_item_id=k[1], net=v, first_seen=first.get(k))
        for k, v in nets.items()
        if v > 0
    ]
    open_lines.sort(key=lambda l: (l.first_seen or "", l.order_id))
    return open_lines


# --------------------------------------------------------------------------
# Shopify side
# --------------------------------------------------------------------------

def _line_numeric_id(gid: str) -> Optional[int]:
    """`gid://shopify/LineItem/123` -> 123."""
    if not gid:
        return None
    tail = str(gid).rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def classify_line(order: Optional[Dict[str, Any]], line_item_id: int) -> Tuple[str, Dict[str, Any]]:
    """Decide the verdict for one ledger-open line. Pure."""
    if not order:
        return "order_missing", {}

    if order.get("cancelledAt"):
        return "cancelled", {
            "order_name": order.get("name"),
            "cancelled_at": order.get("cancelledAt"),
        }

    nodes = ((order.get("lineItems") or {}).get("nodes")) or []
    match = next((n for n in nodes if _line_numeric_id(n.get("id")) == line_item_id), None)
    if match is None:
        return "line_missing", {"order_name": order.get("name")}

    detail = {
        "order_name": order.get("name"),
        "quantity": match.get("quantity"),
        "current_quantity": match.get("currentQuantity"),
        "unfulfilled_quantity": match.get("unfulfilledQuantity"),
        "fulfillment_status": order.get("displayFulfillmentStatus"),
        "financial_status": order.get("displayFinancialStatus"),
    }

    current = match.get("currentQuantity")
    if current is not None and int(current) == 0:
        return "refunded", detail

    unfulfilled = match.get("unfulfilledQuantity")
    if unfulfilled is not None and int(unfulfilled) == 0:
        return "fulfilled", detail

    return "open", detail


def fetch_orders(client, domain: str, token: str, version: str, order_ids: List[int],
                 verbose: bool = False) -> Dict[int, Optional[Dict[str, Any]]]:
    url = f"https://{domain}/admin/api/{version}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    out: Dict[int, Optional[Dict[str, Any]]] = {}

    for i, order_id in enumerate(order_ids, start=1):
        payload = {
            "query": ORDER_QUERY,
            "variables": {"id": f"gid://shopify/Order/{order_id}"},
        }
        resp = client.post(url, headers=headers, json=payload)
        body: Dict[str, Any] = {}
        try:
            body = resp.json()
        except Exception:
            pass

        if resp.status_code != 200 or body.get("errors"):
            raise RuntimeError(
                f"Shopify lookup failed for order {order_id}: "
                f"HTTP {resp.status_code} {json.dumps(body.get('errors') or body)[:400]}\n"
                "A field error here usually means the Admin API version moved; "
                "check ORDER_QUERY against the resolved version."
            )

        out[order_id] = ((body.get("data") or {}).get("order"))
        if verbose:
            print(f"  [{i}/{len(order_ids)}] order {order_id}", file=sys.stderr)

    return out


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

VERDICT_ORDER = ["open", "cancelled", "fulfilled", "refunded", "line_missing", "order_missing"]


def render(lines: List[OpenLine]) -> str:
    out: List[str] = []
    out.append(f"{'ordered':<12} {'order':<16} {'line':<17} {'net':>4}  verdict")
    out.append("-" * 72)
    for l in lines:
        ordered = (l.first_seen or "")[:10]
        out.append(
            f"{ordered:<12} {l.order_id:<16} {l.line_item_id:<17} {l.net:>4}  "
            f"{l.verdict}"
            + (f"  ({l.detail.get('order_name')})" if l.detail.get("order_name") else "")
        )

    counts = {v: 0 for v in VERDICT_ORDER}
    units = {v: 0 for v in VERDICT_ORDER}
    for l in lines:
        counts.setdefault(l.verdict, 0)
        units.setdefault(l.verdict, 0)
        counts[l.verdict] += 1
        units[l.verdict] += l.net

    out.append("")
    out.append("Summary")
    for v in VERDICT_ORDER:
        if counts.get(v):
            out.append(f"  {v:<14} {counts[v]:>4} lines  {units[v]:>4} units")
    for v in sorted(set(counts) - set(VERDICT_ORDER)):
        if counts[v]:
            out.append(f"  {v:<14} {counts[v]:>4} lines  {units[v]:>4} units")

    real = units.get("open", 0)
    stale = sum(units[v] for v in units if v != "open")
    out.append("")
    out.append(f"  FULFIL {real} unit(s). {stale} unit(s) of ledger residue — do not fulfil, repair.")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--product-id", type=int, required=True)
    ap.add_argument("--json", dest="json_path", help="also write the result as JSON here")
    ap.add_argument("--verbose", action="store_true", help="log each Shopify lookup")
    args = ap.parse_args(argv)

    import httpx
    from supabase import create_client

    from shopify_token import get_token_sync, normalize_domain
    from shopify_version import get_api_version

    shop = os.getenv("SHOP_URL")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    missing = [
        name
        for name, value in (
            ("SHOP_URL", shop),
            ("SUPABASE_URL", supabase_url),
            ("SUPABASE_SERVICE_ROLE_KEY", supabase_key),
        )
        if not value
    ]
    if missing:
        print(f"Not set: {', '.join(missing)}", file=sys.stderr)
        return 2

    supabase = create_client(supabase_url, supabase_key)
    rows = fetch_ledger_rows(supabase, args.product_id)
    lines = compute_open_lines(rows)

    print(f"product_id={args.product_id}")
    print(f"ledger rows (clean topics): {len(rows)}")
    print(f"open lines by ledger arithmetic: {len(lines)} ({sum(l.net for l in lines)} units)")
    if not lines:
        print("Nothing open. Nothing to verify.")
        return 0

    domain = normalize_domain(shop)
    version = get_api_version()
    token = get_token_sync()
    order_ids = sorted({l.order_id for l in lines})
    print(f"checking {len(order_ids)} order(s) in Shopify at {version}...")

    try:
        with httpx.Client(timeout=60) as client:
            orders = fetch_orders(client, domain, token, version, order_ids, args.verbose)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for line in lines:
        line.verdict, line.detail = classify_line(orders.get(line.order_id), line.line_item_id)

    print()
    print(render(lines))

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(
                {
                    "product_id": args.product_id,
                    "lines": [
                        {
                            "order_id": l.order_id,
                            "line_item_id": l.line_item_id,
                            "net": l.net,
                            "first_seen": l.first_seen,
                            "verdict": l.verdict,
                            "detail": l.detail,
                        }
                        for l in lines
                    ],
                },
                indent=2,
            )
        )
        print(f"\nJSON written to {args.json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
