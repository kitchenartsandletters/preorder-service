#!/usr/bin/env python3
"""
scripts/repair_missing_fulfillments.py

Repair R3 — and the pilot for the Landmine 19 pass.

Writes the `preorder.tracking` rows for fulfillments that happened in Shopify
but never reached the ledger, then lets the standard 5-minute builder derive
the commitment rows. DRY RUN BY DEFAULT: nothing is written without --apply.

WHY TRACKING ROWS AND NOT LEDGER ROWS
-------------------------------------
Same reason as R1 and R2: one code path stays authoritative. The builder
(`build_commitment_ledger.py`) is the only thing that writes
`preorder.commitment_ledger`, and its per-topic unique indexes are what make
double counting impossible. Writing ledger rows directly would bypass both.

HOW THIS DIFFERS FROM R2 — READ BEFORE CHANGING
-----------------------------------------------
R2 rebuilt from `public.webhook_logs` and stored the **whole original order
payload** on each repair row. That works, but only by a mechanism worth not
relying on: `ledger_tracking_unique` is a UNIQUE index on
`commitment_ledger.tracking_id`, so **one tracking row yields at most ONE
ledger row**. With an 11-line payload the builder attempts 11 inserts and only
the first non-conflicting one lands. R2 recorded several lines of one order by
writing several tracking rows and letting `cl_unique_orders_fulfilled`
(topic, fulfillment_id, line_item_id) reject the already-present lines so that
successive tracking rows landed successive lines. Correct in the end, but it
depends on insert ordering.

This script does not rely on that. **Each tracking row carries exactly ONE
line item** — the one being repaired — so the mapping is deterministic:
one tracking row, one ledger row, the intended line. The payload is therefore
minimal and synthesized, not a captured webhook. That is deliberate: these
fulfillments are missing from `webhook_logs` (which is why they were lost), so
there is no captured payload to replay. Shopify is the source.

PAYLOAD SHAPE, AND WHY EACH FIELD IS THERE
------------------------------------------
    {
      "id": <order_id>,              # builder reads payload.id as order_id
      "name": "#1234",               # traceability only
      "fulfilled_at": <ISO ts>,      # becomes commitment_ledger.occurred_at
      "fulfillment": {"id": <fid>, "created_at": <ISO ts>},
      "line_items": [{ "id": <line_item_id>,
                       "product_id": <product_id>,
                       "variant_id": <variant_id>,
                       "quantity": <fulfilled qty>,
                       "fulfilled_quantity": <fulfilled qty> }]
    }

Deliberately **no top-level `created_at`**. `pick_occurred_at` tries
`created_at` FIRST for `orders/fulfilled`, so an order-creation timestamp there
would date the fulfillment at order time. `fulfilled_at` is the next candidate
and is the correct instant.

`fulfillment.id` is mandatory, not decorative: it is two thirds of
`cl_unique_orders_fulfilled`, the index that makes a re-run harmless. A null
fulfillment_id would make that partial index inapplicable and reopen double
counting.

IDEMPOTENCY
-----------
Two layers.

1. `event_id` is a deterministic UUID5 of
   (order_id, line_item_id, fulfillment_id), and `preorder.tracking` is unique
   on `(event_id, line_item_id)`, so re-running cannot create a second tracking
   row for the same repair.
2. `cl_unique_orders_fulfilled (topic, fulfillment_id, line_item_id)` stops a
   second ledger row for the same fulfilled line however it arrives.

INPUT
-----
The JSON written by `scripts/verify_open_commitment_lines.py`. Only lines that
verifier labelled `fulfilled` are candidates, so a repair can never run on
unverified ledger arithmetic. Re-verify before applying if the JSON is stale.

USAGE
-----
    python scripts/verify_open_commitment_lines.py --product-id 6598890029189 \\
        --json /tmp/septime_open.json

    # dry run (default) — prints exactly what would be written
    python scripts/repair_missing_fulfillments.py --from-json /tmp/septime_open.json

    # write, after the dry run has been reviewed
    python scripts/repair_missing_fulfillments.py --from-json /tmp/septime_open.json --apply

Exit codes: 0 fine, 2 configuration/lookup failure, 3 a guard refused the write.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

TOPIC = "orders/fulfilled"

# Stable namespace for deterministic event ids. Changing this value breaks
# idempotency with every repair row already written — do not change it.
EVENT_NAMESPACE = uuid.UUID("6f0a1d2e-9c4b-5a7e-8d13-2b6c4f9a0e55")

REPAIR_MARKER = (
    "repair {date}: R3 missing orders/fulfilled; fulfilled in Shopify but never "
    "logged (Landmine 19 pilot); rebuilt from Shopify Admin API fulfillment "
    "{fulfillment_id}"
)

ORDER_QUERY = """
query R3OrderFulfillments($id: ID!) {
  order(id: $id) {
    id
    name
    cancelledAt
    lineItems(first: 100) {
      nodes {
        id
        quantity
        currentQuantity
        unfulfilledQuantity
        product { id }
        variant { id }
      }
    }
    fulfillments(first: 50) {
      id
      createdAt
      status
      fulfillmentLineItems(first: 100) {
        nodes {
          quantity
          lineItem { id }
        }
      }
    }
  }
}
"""


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def numeric_id(gid: Any) -> Optional[int]:
    """`gid://shopify/Thing/123` -> 123. Exact tail only, never a substring."""
    if gid is None:
        return None
    tail = str(gid).rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def deterministic_event_id(order_id: int, line_item_id: int, fulfillment_id: int) -> str:
    return str(uuid.uuid5(EVENT_NAMESPACE, f"r3:{order_id}:{line_item_id}:{fulfillment_id}"))


def load_candidates(path: Path) -> Tuple[int, List[Dict[str, Any]]]:
    """Read the verifier's JSON and keep only the lines it labelled `fulfilled`."""
    data = json.loads(path.read_text())
    product_id = int(data["product_id"])
    candidates = [
        line for line in data.get("lines", []) if line.get("verdict") == "fulfilled"
    ]
    return product_id, candidates


def iter_fulfillments(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`fulfillments` is a plain list in current API versions and a connection
    in older ones. Accept both rather than guessing from the version."""
    f = order.get("fulfillments")
    if isinstance(f, list):
        return [x for x in f if isinstance(x, dict)]
    if isinstance(f, dict):
        return [x for x in (f.get("nodes") or []) if isinstance(x, dict)]
    return []


def iter_line_items(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    li = order.get("lineItems")
    if isinstance(li, dict):
        return [x for x in (li.get("nodes") or []) if isinstance(x, dict)]
    if isinstance(li, list):
        return [x for x in li if isinstance(x, dict)]
    return []


def iter_fulfillment_lines(fulfillment: Dict[str, Any]) -> List[Dict[str, Any]]:
    fli = fulfillment.get("fulfillmentLineItems")
    if isinstance(fli, dict):
        return [x for x in (fli.get("nodes") or []) if isinstance(x, dict)]
    if isinstance(fli, list):
        return [x for x in fli if isinstance(x, dict)]
    return []


@dataclass
class RepairRow:
    order_id: int
    order_name: Optional[str]
    line_item_id: int
    product_id: int
    variant_id: Optional[int]
    fulfillment_id: int
    fulfilled_at: str
    quantity: int
    event_id: str = ""
    skip_reason: Optional[str] = None

    def tracking_row(self, shop_domain: str, marker_date: str) -> Dict[str, Any]:
        payload = {
            "id": self.order_id,
            "name": self.order_name,
            # No top-level created_at on purpose: pick_occurred_at prefers it
            # and it would date the fulfillment at order-creation time.
            "fulfilled_at": self.fulfilled_at,
            "fulfillment": {"id": self.fulfillment_id, "created_at": self.fulfilled_at},
            "line_items": [
                {
                    "id": self.line_item_id,
                    "product_id": self.product_id,
                    "variant_id": self.variant_id,
                    "quantity": self.quantity,
                    "fulfilled_quantity": self.quantity,
                }
            ],
        }
        return {
            "event_id": self.event_id,
            "topic": TOPIC,
            "shop_domain": shop_domain,
            "source_service": "repair-r3",
            "order_id": self.order_id,
            "order_name": self.order_name,
            "line_item_id": self.line_item_id,
            "product_id": self.product_id,
            "variant_id": self.variant_id,
            "quantity": self.quantity,
            "payload": payload,
            "processing_notes": REPAIR_MARKER.format(
                date=marker_date, fulfillment_id=self.fulfillment_id
            ),
        }


@dataclass
class Plan:
    rows: List[RepairRow] = field(default_factory=list)
    skipped: List[RepairRow] = field(default_factory=list)
    refusals: List[str] = field(default_factory=list)


def build_plan(
    product_id: int,
    candidates: List[Dict[str, Any]],
    orders: Dict[int, Optional[Dict[str, Any]]],
) -> Plan:
    """Turn verified-fulfilled lines into the tracking rows to write. Pure."""
    plan = Plan()

    for cand in candidates:
        order_id = int(cand["order_id"])
        line_item_id = int(cand["line_item_id"])
        order = orders.get(order_id)

        if order is None:
            plan.refusals.append(
                f"order {order_id}: not found in Shopify; refusing to repair a line "
                "whose order cannot be read"
            )
            continue

        if order.get("cancelledAt"):
            plan.refusals.append(
                f"order {order_id} ({order.get('name')}): cancelled in Shopify, but the "
                "verifier labelled this line fulfilled. The JSON is stale — re-verify."
            )
            continue

        line = next(
            (li for li in iter_line_items(order) if numeric_id(li.get("id")) == line_item_id),
            None,
        )
        if line is None:
            plan.refusals.append(
                f"order {order_id} ({order.get('name')}): line {line_item_id} is not on the "
                "order any more; re-verify"
            )
            continue

        unfulfilled = line.get("unfulfilledQuantity")
        if unfulfilled is not None and int(unfulfilled) > 0:
            plan.refusals.append(
                f"order {order_id} ({order.get('name')}): line {line_item_id} still has "
                f"{unfulfilled} unfulfilled in Shopify. This line is NOT settled — "
                "repairing it would subtract a commitment that is still owed."
            )
            continue

        product_gid_id = numeric_id((line.get("product") or {}).get("id"))
        if product_gid_id is None or product_gid_id != product_id:
            plan.refusals.append(
                f"order {order_id} ({order.get('name')}): line {line_item_id} belongs to "
                f"product {product_gid_id}, not {product_id}; refusing"
            )
            continue
        variant_id = numeric_id((line.get("variant") or {}).get("id"))

        # One row per (fulfillment, line): a line split across two shipments is
        # two fulfillments, and each carries its own quantity.
        matched = False
        for ful in iter_fulfillments(order):
            fulfillment_id = numeric_id(ful.get("id"))
            if fulfillment_id is None:
                continue
            for fline in iter_fulfillment_lines(ful):
                if numeric_id((fline.get("lineItem") or {}).get("id")) != line_item_id:
                    continue
                qty = fline.get("quantity")
                if qty is None or int(qty) <= 0:
                    continue
                matched = True
                row = RepairRow(
                    order_id=order_id,
                    order_name=order.get("name"),
                    line_item_id=line_item_id,
                    product_id=product_id,
                    variant_id=variant_id,
                    fulfillment_id=fulfillment_id,
                    fulfilled_at=str(ful.get("createdAt")),
                    quantity=int(qty),
                )
                row.event_id = deterministic_event_id(order_id, line_item_id, fulfillment_id)
                plan.rows.append(row)

        if not matched:
            plan.refusals.append(
                f"order {order_id} ({order.get('name')}): line {line_item_id} reads as "
                "fulfilled but no fulfillment in Shopify contains it; refusing to invent "
                "a fulfillment id"
            )

    return plan


def check_totals(plan: Plan, candidates: List[Dict[str, Any]]) -> List[str]:
    """Range guards, not a frozen count.

    R2's frozen-count guard refused a correct write because a fulfillment had
    landed legitimately between the dry run and the apply. These compare the
    plan against the candidate set instead of against a number recorded earlier.
    """
    problems: List[str] = []
    expected_units = sum(int(c.get("net") or 0) for c in candidates)
    planned_units = sum(r.quantity for r in plan.rows)

    if planned_units > expected_units:
        problems.append(
            f"planned {planned_units} units against {expected_units} open units in the "
            "verifier JSON. A repair must never subtract more than was open."
        )
    if plan.rows and len({(r.event_id, r.line_item_id) for r in plan.rows}) != len(plan.rows):
        problems.append("duplicate (event_id, line_item_id) in the plan; the tracking insert would conflict")
    return problems


# --------------------------------------------------------------------------
# IO
# --------------------------------------------------------------------------

def fetch_orders(client, domain: str, token: str, version: str,
                 order_ids: List[int]) -> Dict[int, Optional[Dict[str, Any]]]:
    url = f"https://{domain}/admin/api/{version}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    out: Dict[int, Optional[Dict[str, Any]]] = {}
    for order_id in order_ids:
        resp = client.post(
            url,
            headers=headers,
            json={"query": ORDER_QUERY, "variables": {"id": f"gid://shopify/Order/{order_id}"}},
        )
        body: Dict[str, Any] = {}
        try:
            body = resp.json()
        except Exception:
            pass
        if resp.status_code != 200 or body.get("errors"):
            raise RuntimeError(
                f"Shopify lookup failed for order {order_id}: HTTP {resp.status_code} "
                f"{json.dumps(body.get('errors') or body)[:400]}\n"
                "A field error usually means the Admin API version moved; check ORDER_QUERY."
            )
        out[order_id] = (body.get("data") or {}).get("order")
    return out


def existing_ledger_keys(supabase, product_id: int) -> set:
    """(fulfillment_id, line_item_id) already recorded, so the dry run reports
    only genuinely missing rows rather than ones the unique index would drop."""
    keys = set()
    page_size = 1000
    for page in range(100):
        start = page * page_size
        resp = (
            supabase.schema("preorder")
            .table("commitment_ledger")
            .select("fulfillment_id,line_item_id")
            .eq("product_id", product_id)
            .eq("topic", TOPIC)
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = resp.data or []
        for r in batch:
            keys.add((r.get("fulfillment_id"), r.get("line_item_id")))
        if len(batch) < page_size:
            return keys
    raise RuntimeError("more ledger pages than expected; raise the page cap")


def render(plan: Plan, already: int) -> str:
    out: List[str] = []
    if plan.rows:
        out.append(f"{'order':<16} {'line':<17} {'fulfillment':<16} {'qty':>4}  fulfilled_at")
        out.append("-" * 78)
        for r in plan.rows:
            out.append(
                f"{r.order_id:<16} {r.line_item_id:<17} {r.fulfillment_id:<16} "
                f"{r.quantity:>4}  {r.fulfilled_at}"
            )
    out.append("")
    out.append(f"to write:            {len(plan.rows)} tracking row(s), "
               f"{sum(r.quantity for r in plan.rows)} unit(s) to be subtracted")
    out.append(f"already in ledger:   {already} line(s) (the unique index would reject these)")
    if plan.refusals:
        out.append("")
        out.append(f"REFUSALS ({len(plan.refusals)}):")
        for r in plan.refusals:
            out.append(f"  - {r}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--from-json", dest="from_json", required=True,
                    help="JSON written by verify_open_commitment_lines.py")
    ap.add_argument("--apply", action="store_true",
                    help="actually write the tracking rows (default: dry run)")
    ap.add_argument("--allow-refusals", action="store_true",
                    help="write the clean rows even if some lines were refused")
    args = ap.parse_args(argv)

    import httpx
    from datetime import date as _date

    from dotenv import load_dotenv
    from supabase import create_client

    from shopify_token import get_token_sync, normalize_domain
    from shopify_version import get_api_version

    load_dotenv(REPO / ".env", override=False)

    # The input is validated before the credentials, so a wrong --from-json
    # path reports the real problem, and a run with nothing to repair exits
    # cleanly without needing any.
    json_path = Path(args.from_json)
    if not json_path.exists():
        print(f"No such file: {json_path}\nRun verify_open_commitment_lines.py first.",
              file=sys.stderr)
        return 2

    try:
        product_id, candidates = load_candidates(json_path)
    except (ValueError, KeyError, TypeError) as exc:
        print(f"{json_path} is not a verifier JSON file: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    print(f"product_id={product_id}")
    print(f"candidates labelled `fulfilled` by the verifier: {len(candidates)}")
    if not candidates:
        print("Nothing to repair.")
        return 0

    shop = os.getenv("SHOP_URL")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    missing = [n for n, v in (("SHOP_URL", shop), ("SUPABASE_URL", supabase_url),
                              ("SUPABASE_SERVICE_ROLE_KEY", supabase_key)) if not v]
    if missing:
        print(f"Not set: {', '.join(missing)}", file=sys.stderr)
        return 2

    domain = normalize_domain(shop)
    version = get_api_version()
    try:
        token = get_token_sync()
    except Exception as exc:
        print(f"Could not get a Shopify token: {type(exc).__name__}: {exc}\n"
              "Check SHOP_URL, SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET.",
              file=sys.stderr)
        return 2

    order_ids = sorted({int(c["order_id"]) for c in candidates})
    print(f"reading {len(order_ids)} order(s) from Shopify at {version}...")
    try:
        with httpx.Client(timeout=60) as client:
            orders = fetch_orders(client, domain, token, version, order_ids)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    plan = build_plan(product_id, candidates, orders)

    try:
        supabase = create_client(supabase_url, supabase_key)
        already_keys = existing_ledger_keys(supabase, product_id)
    except Exception as exc:
        print(f"Could not read the ledger: {type(exc).__name__}: {exc}\n"
              "Check SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.", file=sys.stderr)
        return 2

    kept, already = [], 0
    for row in plan.rows:
        if (row.fulfillment_id, row.line_item_id) in already_keys:
            already += 1
        else:
            kept.append(row)
    plan.rows = kept

    print()
    print(render(plan, already))

    problems = check_totals(plan, candidates)
    if problems:
        print()
        print("GUARD REFUSED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 3

    if plan.refusals and not args.allow_refusals:
        print()
        print("Refusing to write: some lines were refused above. Re-verify, or pass "
              "--allow-refusals to write only the clean rows.", file=sys.stderr)
        return 3

    if not plan.rows:
        print("\nNothing to write.")
        return 0

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write these rows.")
        print("The 5-minute builder cron derives the ledger rows afterwards; the")
        print("replay cursor picks up rows created after it, so no manual step.")
        return 0

    marker_date = _date.today().isoformat()
    rows = [r.tracking_row(domain, marker_date) for r in plan.rows]
    resp = (
        supabase.schema("preorder")
        .table("tracking")
        .insert(rows)
        .execute()
    )
    written = len(resp.data or [])
    print(f"\nwrote {written} tracking row(s)")
    if written != len(rows):
        print(f"WARNING: attempted {len(rows)} but wrote {written}. The "
              "(event_id, line_item_id) unique constraint absorbs a re-run, so this is "
              "expected if some rows were already repaired. Verify before doing anything else.",
              file=sys.stderr)
    print(f"\nFind them with:\n  select * from preorder.tracking "
          f"where processing_notes like 'repair {marker_date}: R3%';")
    print("\nThe builder cron runs every 5 minutes. Re-run "
          "verify_open_commitment_lines.py afterwards to confirm the lines closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
