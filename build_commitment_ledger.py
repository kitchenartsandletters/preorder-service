#!/usr/bin/env python3
"""
Phase 12.5 — Step 3
Replay preorder.tracking into preorder.commitment_ledger (idempotent).

Topics supported:
- orders/create        => +qty per line item
- orders/paid          => +qty per line item (alternate positive commitment source)
- orders/fulfilled     => -fulfilled_qty per line item (best-effort)
- orders/cancelled     => -unfulfilled_qty per line item (best-effort)
- refunds/create       => -refund_line_item.quantity per refund line item

Idempotency:
- Uses natural per-topic keys in commitment_ledger to dedupe repeated webhook deliveries.
- Insert uses `ON CONFLICT DO NOTHING` so any matching unique constraint will suppress duplicates.

Ordering:
- Keyset paginated by (created_at, id) ascending for deterministic replay.

Usage:
  python build_commitment_ledger.py --topic orders/create
  python build_commitment_ledger.py --topic all --limit 2000
  python build_commitment_ledger.py --since 2026-01-01T00:00:00Z
  python build_commitment_ledger.py --dry-run
"""

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dateutil.parser import isoparse

from db.connection import get_pool


SUPPORTED_TOPICS = {
    "orders/create",
    "orders/paid",        # alternate positive commitment source (draft-order conversions)
    "orders/fulfilled",
    "orders/cancelled",
    "refunds/create",
}


# -----------------------------
# Helpers
# -----------------------------

def _as_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def _parse_ts(value: Any) -> Optional[datetime]:
    """
    Accepts ISO timestamps or datetime, returns tz-aware UTC datetime.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            dt = isoparse(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None


def pick_occurred_at(topic: str, payload: Dict[str, Any], fallback_created_at: datetime) -> datetime:
    """
    Best-effort occurred_at selection per topic.
    Fallback: tracking.created_at.
    """
    candidates: List[Any] = []

    if topic in ("orders/create", "orders/paid"):
        candidates = [
            payload.get("created_at"),
            payload.get("createdAt"),
            payload.get("processed_at"),
            payload.get("processedAt"),
            payload.get("updated_at"),
            payload.get("updatedAt"),
        ]
    elif topic == "orders/fulfilled":
        # Shopify fulfillment webhooks vary; try common keys
        candidates = [
            payload.get("created_at"),
            payload.get("createdAt"),
            payload.get("fulfilled_at"),
            payload.get("fulfilledAt"),
            payload.get("updated_at"),
            payload.get("updatedAt"),
        ]
        # Sometimes nested under "fulfillment"
        f = payload.get("fulfillment")
        if isinstance(f, dict):
            candidates = candidates + [
                f.get("created_at"),
                f.get("createdAt"),
                f.get("updated_at"),
                f.get("updatedAt"),
            ]
    elif topic == "orders/cancelled":
        candidates = [payload.get("cancelled_at"), payload.get("cancelledAt"), payload.get("updated_at"), payload.get("updatedAt")]
    elif topic == "refunds/create":
        candidates = [payload.get("processed_at"), payload.get("processedAt"), payload.get("created_at"), payload.get("createdAt")]

    for c in candidates:
        dt = _parse_ts(c)
        if dt is not None:
            return dt

    # fallback
    if fallback_created_at.tzinfo is None:
        return fallback_created_at.replace(tzinfo=timezone.utc)
    return fallback_created_at.astimezone(timezone.utc)


def iter_order_line_items(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """
    Orders payload usually includes 'line_items'.
    """
    items = payload.get("line_items")
    if isinstance(items, list):
        for li in items:
            if isinstance(li, dict):
                yield li


def iter_fulfilled_line_items(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """
    Fulfillment webhook shapes vary.

    Common patterns:
    - payload.line_items
    - payload.fulfillment.line_items
    - payload.fulfillment.line_items with qty fields
    """
    items = payload.get("line_items")
    if isinstance(items, list):
        for li in items:
            if isinstance(li, dict):
                yield li
        return

    f = payload.get("fulfillment")
    if isinstance(f, dict):
        f_items = f.get("line_items")
        if isinstance(f_items, list):
            for li in f_items:
                if isinstance(li, dict):
                    yield li


def iter_refund_line_items(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """
    Refunds payload commonly includes refund_line_items:
    [
      { "line_item_id": ..., "quantity": ..., "line_item": {...} }
    ]
    """
    rlis = payload.get("refund_line_items")
    if isinstance(rlis, list):
        for rli in rlis:
            if isinstance(rli, dict):
                yield rli


def compute_fulfilled_qty(li: Dict[str, Any]) -> Optional[int]:
    """
    Best-effort fulfilled quantity.
    Prefer explicit fulfilled_quantity-like fields, else fall back to quantity.
    """
    for key in ("fulfilled_quantity", "fulfilledQuantity", "quantity", "qty"):
        v = li.get(key)
        q = _as_int(v)
        if q is not None:
            return q
    return None


def compute_unfulfilled_qty(li: Dict[str, Any]) -> Optional[int]:
    """
    Best-effort unfulfilled quantity at cancellation time.
    Prefer fulfillable_quantity/remaining quantities, else fall back to quantity.
    """
    for key in ("fulfillable_quantity", "fulfillableQuantity", "unfulfilled_quantity", "remaining_quantity", "quantity", "qty"):
        v = li.get(key)
        q = _as_int(v)
        if q is not None:
            return q
    return None


@dataclass
class LedgerRow:
    tracking_id: str
    event_id: Optional[str]
    topic: str
    product_id: int
    variant_id: Optional[int]
    order_id: Optional[int]
    line_item_id: Optional[int]
    fulfillment_id: Optional[int]
    refund_id: Optional[int]
    delta_qty: int
    occurred_at: datetime
# -----------------------------
# Extraction per topic
# -----------------------------

def extract_refund_id(payload: Dict[str, Any]) -> Optional[int]:
    # refunds/create payload id is the refund id
    return _as_int(payload.get("id") or payload.get("refund_id") or payload.get("refundId"))


def extract_fulfillment_id(payload: Dict[str, Any]) -> Optional[int]:
    # orders/fulfilled payload shapes vary.
    # Sometimes a nested payload.fulfillment exists; sometimes it's an order object with a fulfillments array.
    f = payload.get("fulfillment")
    if isinstance(f, dict):
        fid = _as_int(f.get("id") or f.get("fulfillment_id") or f.get("fulfillmentId"))
        if fid is not None:
            return fid

    fs = payload.get("fulfillments")
    if isinstance(fs, list) and fs:
        first = fs[0]
        if isinstance(first, dict):
            fid = _as_int(first.get("id") or first.get("fulfillment_id") or first.get("fulfillmentId"))
            if fid is not None:
                return fid

    return _as_int(payload.get("fulfillment_id") or payload.get("fulfillmentId"))


# -----------------------------
# Extraction per topic
# -----------------------------

def extract_ledger_rows(
    tracking_id: str,
    event_id: Optional[str],
    topic: str,
    payload: Dict[str, Any],
    created_at: datetime,
) -> List[LedgerRow]:
    rows: List[LedgerRow] = []
    occurred_at = pick_occurred_at(topic, payload, created_at)

    # Resolve order_id safely. For order webhooks, payload.id is the order id.
    order_id = None
    if topic != "refunds/create":
        order_id = _as_int(payload.get("id") or payload.get("order_id") or payload.get("orderId"))
    # Natural ids (used for dedupe / traceability)
    fulfillment_id = extract_fulfillment_id(payload) if topic == "orders/fulfilled" else None
    refund_id = extract_refund_id(payload) if topic == "refunds/create" else None

    # Refund payloads must resolve order_id from explicit order references.
    # Never fall back to payload.id here because that is the refund id.
    if topic == "refunds/create":
        order_id = _as_int(payload.get("order_id") or payload.get("orderId"))
        if order_id is None:
            order_obj = payload.get("order")
            if isinstance(order_obj, dict):
                order_id = _as_int(order_obj.get("id"))

    # orders/paid is treated as an alternate positive commitment source.
    # Idempotency is enforced downstream by the commitment_ledger unique index
    # so that only one positive commitment per (order_id, line_item_id) is stored.
    if topic in ("orders/create", "orders/paid"):
        for li in iter_order_line_items(payload):
            product_id = _as_int(li.get("product_id") or li.get("productId"))
            if product_id is None:
                continue
            variant_id = _as_int(li.get("variant_id") or li.get("variantId"))
            line_item_id = _as_int(li.get("id") or li.get("line_item_id") or li.get("lineItemId"))
            qty = _as_int(li.get("quantity"))
            if qty is None or qty == 0:
                continue

            rows.append(LedgerRow(
                tracking_id=tracking_id,
                event_id=event_id,
                topic=topic,
                product_id=product_id,
                variant_id=variant_id,
                order_id=order_id,
                line_item_id=line_item_id,
                fulfillment_id=fulfillment_id,
                refund_id=refund_id,
                delta_qty=+qty,
                occurred_at=occurred_at,
            ))

    elif topic == "orders/fulfilled":
        for li in iter_fulfilled_line_items(payload):
            product_id = _as_int(li.get("product_id") or li.get("productId"))
            if product_id is None:
                continue
            variant_id = _as_int(li.get("variant_id") or li.get("variantId"))
            line_item_id = _as_int(li.get("id") or li.get("line_item_id") or li.get("lineItemId"))
            fulfilled_qty = compute_fulfilled_qty(li)
            if fulfilled_qty is None or fulfilled_qty == 0:
                continue

            rows.append(LedgerRow(
                tracking_id=tracking_id,
                event_id=event_id,
                topic=topic,
                product_id=product_id,
                variant_id=variant_id,
                order_id=order_id,
                line_item_id=line_item_id,
                fulfillment_id=fulfillment_id,
                refund_id=refund_id,
                delta_qty=-fulfilled_qty,
                occurred_at=occurred_at,
            ))

    elif topic == "orders/cancelled":
        for li in iter_order_line_items(payload):
            product_id = _as_int(li.get("product_id") or li.get("productId"))
            if product_id is None:
                continue
            variant_id = _as_int(li.get("variant_id") or li.get("variantId"))
            line_item_id = _as_int(li.get("id") or li.get("line_item_id") or li.get("lineItemId"))
            unfulfilled_qty = compute_unfulfilled_qty(li)
            if unfulfilled_qty is None or unfulfilled_qty == 0:
                continue

            rows.append(LedgerRow(
                tracking_id=tracking_id,
                event_id=event_id,
                topic=topic,
                product_id=product_id,
                variant_id=variant_id,
                order_id=order_id,
                line_item_id=line_item_id,
                fulfillment_id=fulfillment_id,
                refund_id=refund_id,
                delta_qty=-unfulfilled_qty,
                occurred_at=occurred_at,
            ))

    elif topic == "refunds/create":
        # Hard safety check: refunds must have both a refund_id and order_id.
        # Historical corruption occurred when payload.id (refund id) was used
        # as order_id. If we cannot resolve a proper order_id, skip the event.
        if refund_id is None or order_id is None:
            return rows
        for rli in iter_refund_line_items(payload):
            qty = _as_int(rli.get("quantity"))
            if qty is None or qty == 0:
                continue

            li = rli.get("line_item")
            if not isinstance(li, dict):
                # sometimes fields are top-level on refund_line_item
                li = rli

            product_id = _as_int(li.get("product_id") or li.get("productId"))
            if product_id is None:
                continue
            variant_id = _as_int(li.get("variant_id") or li.get("variantId"))
            # Prefer explicit refund_line_item reference, then nested line_item id
            line_item_id = _as_int(
                rli.get("line_item_id")
                or li.get("id")
                or li.get("line_item_id")
                or li.get("lineItemId")
            )
            if line_item_id is None:
                continue

            rows.append(LedgerRow(
                tracking_id=tracking_id,
                event_id=event_id,
                topic=topic,
                product_id=product_id,
                variant_id=variant_id,
                order_id=order_id,
                line_item_id=line_item_id,
                fulfillment_id=fulfillment_id,
                refund_id=refund_id,
                delta_qty=-qty,
                occurred_at=occurred_at,
            ))

    return rows
    # Invariant for refunds/create rows:
    #   order_id  -> Shopify Order ID
    #   refund_id -> Shopify Refund ID
    # These must never be swapped. Historical corruption occurred when
    # payload.id (refund id) was incorrectly treated as order_id.

# -----------------------------
# DB IO
# -----------------------------

FETCH_BATCH_SQL = """
select
  id,
  event_id,
  topic,
  created_at
from preorder.tracking
where topic = $1
  and ($2::timestamptz is null or created_at >= $2::timestamptz)
  and (
    ($3::timestamptz is null and $4::uuid is null)
    or (created_at, id) > ($3::timestamptz, $4::uuid)
  )
order by created_at asc, id asc
limit $5
"""

FETCH_PAYLOADS_SQL = """
select id, payload
from preorder.tracking
where id = any($1::uuid[])
"""

INSERT_LEDGER_SQL = """
insert into preorder.commitment_ledger
(tracking_id, event_id, topic, product_id, variant_id, order_id, line_item_id, fulfillment_id, refund_id, delta_qty, occurred_at)
values
($1::uuid, $2::uuid, $3::text, $4::bigint, $5::bigint, $6::bigint, $7::bigint, $8::bigint, $9::bigint, $10::int, $11::timestamptz)
on conflict do nothing
"""


async def insert_rows(pool, rows: List[LedgerRow], dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        return len(rows)

    # Use batch insert for performance instead of row‑by‑row execution.
    params = [
        (
            r.tracking_id,
            r.event_id,
            r.topic,
            r.product_id,
            r.variant_id,
            r.order_id,
            r.line_item_id,
            r.fulfillment_id,
            r.refund_id,
            r.delta_qty,
            r.occurred_at,
        )
        for r in rows
    ]

    await pool.executemany(INSERT_LEDGER_SQL, params)

    # We return the number of attempted inserts (conflicts are ignored by SQL)
    return len(rows)


# -----------------------------
# Runner
# -----------------------------

async def run(topic: str, limit: int, since: Optional[str], dry_run: bool) -> Dict[str, Any]:
    if topic != "all" and topic not in SUPPORTED_TOPICS:
        raise SystemExit(f"Unsupported topic: {topic}. Use one of: {sorted(SUPPORTED_TOPICS)} or 'all'.")

    since_dt = _parse_ts(since) if since else None

    pool = await get_pool()

    topics = sorted(SUPPORTED_TOPICS) if topic == "all" else [topic]
    summary: Dict[str, Any] = {"topics": {}, "dry_run": dry_run, "limit_per_topic": limit, "since": since_dt.isoformat() if since_dt else None}

    for t in topics:
        last_created_at: Optional[datetime] = None
        last_id: Optional[str] = None
        total_tracking_rows = 0
        total_ledger_rows = 0

        while True:
            batch = await pool.fetch(FETCH_BATCH_SQL, t, since_dt, last_created_at, last_id, limit)
            if not batch:
                break

            total_tracking_rows += len(batch)

            ids = [str(r["id"]) for r in batch]

            payload_rows = await pool.fetch(
                FETCH_PAYLOADS_SQL,
                ids
            )

            payload_map = {
                str(r["id"]): r["payload"]
                for r in payload_rows
            }

            to_insert: List[LedgerRow] = []
            for row in batch:
                tracking_id = str(row["id"])
                event_id = str(row["event_id"]) if row.get("event_id") else None
                created_at = row["created_at"]
                payload = payload_map.get(tracking_id)

                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}

                if not isinstance(payload, dict):
                    payload = {}

                to_insert.extend(extract_ledger_rows(
                    tracking_id=tracking_id,
                    event_id=event_id,
                    topic=t,
                    payload=payload,
                    created_at=created_at,
                ))

                last_created_at = created_at
                last_id = tracking_id

            total_ledger_rows += await insert_rows(pool, to_insert, dry_run=dry_run)

        summary["topics"][t] = {
            "tracking_rows_scanned": total_tracking_rows,
            "ledger_rows_emitted": total_ledger_rows,
            "note": "ledger_rows_emitted counts attempted inserts; conflict-do-nothing makes replay safe",
        }

    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="all", help="orders/create | orders/fulfilled | orders/cancelled | refunds/create | all")
    ap.add_argument("--limit", type=int, default=2000, help="batch size for tracking scan")
    ap.add_argument("--since", default=None, help="ISO timestamp lower bound for tracking.created_at (e.g. 2026-01-01T00:00:00Z)")
    ap.add_argument("--dry-run", action="store_true", help="do not write inserts")
    args = ap.parse_args()

    summary = asyncio.run(run(
        topic=args.topic,
        limit=args.limit,
        since=args.since,
        dry_run=args.dry_run,
    ))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()