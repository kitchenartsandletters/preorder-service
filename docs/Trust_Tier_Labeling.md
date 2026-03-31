## Phase 2: Trust-Tier Labeling

This document replaces the README as the authoritative statement of what this system is and is not allowed to claim. It is not aspirational. Every statement here is derived from the Phase 1 audit.

---

### The Cutover Date

**2026-02-11** is the trust boundary for all live webhook capture. Everything before this date in the `commitment_ledger` is either synthetic backfill or pre-capture fulfillment data. Everything on or after this date has verified webhook evidence in `preorder.tracking`.

This date is not negotiable and should not be changed without a new audit.

---

### Trust Tier Definitions

There are three tiers. Every data object in this system belongs to exactly one of them.

**Tier 1 — Verified Event Evidence**
Raw facts captured directly from Shopify webhooks after the cutover date. No interpretation applied. Trust these unconditionally for what they are: a record that Shopify sent this event at this time.

**Tier 2 — Derived Operational State**
Figures computed from Tier 1 data using deterministic rules. Trust these for current operational monitoring, not for historical claims. Their accuracy depends entirely on the completeness of the Tier 1 inputs they were derived from.

**Tier 3 — Estimated or Forced-Balance**
Figures that were reconstructed from incomplete history, inferred from external sources, or inserted to achieve internal consistency. Do not use these for any claim that needs to be defended externally. Label them explicitly wherever they appear in any output.

---

### Object-Level Trust Assignments

**`preorder.tracking` — Tier 1**

Every row is a direct webhook payload received from Shopify. No transformation was applied at ingest. The gateway performs HMAC validation and inserts the raw payload. Rows before 2026-02-11 do not exist in this table — the table starts on cutover day.

Permitted claims: "Shopify sent this event at this timestamp." Nothing more.

**`commitment_ledger` rows where `topic = 'orders/create'` and `occurred_at >= 2026-02-11` — Tier 1**

These rows are direct replays of Tier 1 tracking events. The product ID, order ID, line item ID, and quantity were extracted from verified webhook payloads. The sign convention is confirmed correct: creates are positive.

**`commitment_ledger` rows where `topic = 'orders/fulfilled'` and `occurred_at >= 2026-02-11` — Tier 1**

Fulfillment events captured via live webhook. Confirmed negative sign. No sign violations found in audit.

**`commitment_ledger` rows where `topic IN ('orders/paid', 'refunds/create', 'orders/cancelled')` — Tier 1**

Live webhook events. `orders/paid` only begins appearing from 2026-03-07 when that topic was added to the ingest pipeline. Refunds and cancellations are sparse but correctly signed.

**`commitment_ledger` rows where `topic = 'orders/create'` and `occurred_at < 2026-02-11` — Tier 2**

These rows were inserted by `build_commitment_ledger` replaying tracking events that arrived before the cutover. The tracking table starts on 2026-02-11, so there are no pre-cutover `orders/create` tracking rows. Any `orders/create` ledger row with `occurred_at < 2026-02-11` reflects an order that was placed before capture began but whose webhook was replayed — treat with caution and verify against Shopify if the figure is used in reporting.

**`commitment_ledger` rows where `topic = 'orders/create_backfill'` — Tier 3**

82,107 rows. These are the result of a historical reconstruction operation that compared Shopify's order history against ledger gaps and inserted synthetic positive commitments to fill those gaps. They date back to March 2021. They represent 87% of all positive commitment volume. They are not event evidence. They are estimates of what the ledger should contain based on a point-in-time Shopify snapshot.

Permitted claims: "The system estimates approximately N units were committed for this product before live capture began." No claim about exact historical truth.

**`commitment_ledger` rows where `topic = 'orders/fulfilled'` and `occurred_at < 2026-02-11` — Tier 2/3 mixed**

These are fulfillment events that predate the cutover. Some arrived via live webhook replay; others may have been captured during earlier periods. Their accuracy depends on whether the ingest system was functioning at the time of the underlying event. Do not use alone for fulfillment completeness claims on pre-cutover titles.

**`commitment_ledger` rows where `topic = 'reconciliation.adjustment'` — Tier 3**

95 rows inserted on 2026-03-31. These are balancing entries that forced `ledger_open_qty` (excluding backfill) to match `shopify_open_qty` at the time of the reconciliation run. They are accounting corrections, not business events. They have no `order_id`, no `line_item_id`, no `tracking_id` that points to a real event. Three of the corrected products had negative balances because their fulfillment events outnumbered their live create events — a predictable consequence of backfill-sourced creates being excluded from the reconciliation script's open-commitment calculation.

Permitted claims: "The reconciliation script achieved internal balance on this date." No claim about historical accuracy.

**`preorder.lifecycle_snapshot` — Tier 3**

83 rows. All closed. All `current_committed_qty = 0`. This state was achieved via a combination of live fulfillment events and the 95 reconciliation adjustments. The snapshot represents "the system was made internally consistent on 2026-03-31," not "all preorder obligations have been verified as fulfilled." The `presale_commitment_total` column reflects the ledger state at the time of snapshot creation, which includes backfill rows. It cannot be used as an authoritative presale total.

The schema also differs from documentation: `lifecycle_state` does not exist as a column. The column is `current_committed_qty` combined with the presence of `lifecycle_closed_at`.

**`preorder.pubdate_history` — Tier 2**

Publication dates are present for all products that have ledger activity. The `change_source` column indicates how each date was set. Treat as reliable for operational use but verify the `change_source` value for any title where the pub date is being used in a formal report.

**`preorder.product_status` — Tier 2**

Classification is deterministic given its inputs. The classification logic has been confirmed stable. Trust the current classification of any active product. Do not use historical classification states to reconstruct what a product's status was at a past point in time without also checking `pubdate_history`.

**`preorder.inventory_arrival` — Tier 2**

Records the first positive inventory arrival per product. Used by the snapshotter's closure condition. Trust for operational use. Not independently audited in Phase 1 — if inventory arrival dates are used in external reporting, they should be verified against Shopify inventory history.

---

### What the System Is Allowed to Claim

These are the only claims this system may make in any dashboard, report, or API response without a confidence label attached.

**Allowed without qualification:**

- Current product classification (active preorder, historical preorder, normal)
- Effective pub date for any product currently in the system
- Live order event counts and quantities for orders placed after 2026-02-11
- Whether a product has received its first inventory arrival
- Whether a lifecycle snapshot exists for a product and whether it is closed

**Allowed with a `confidence: estimated` label:**

- Total presale commitment for any title where `orders/create_backfill` rows exist
- Fulfillment completeness for any title whose preorder lifecycle predates 2026-02-11
- Any aggregate presale total that combines live and backfill rows

**Not allowed under any circumstances without a separate verification project:**

- Exact historical presale totals for titles active before 2026-02-11
- Claims that all presale obligations for a given title have been verified as fulfilled
- NYT reporting figures derived from `presale_commitment_total` in `lifecycle_snapshot` without a documented confidence review
- Any figure described as authoritative that passes through `orders/create_backfill` or `reconciliation.adjustment` rows without explicit labeling

---

### Known System Gaps

These are facts established in the Phase 1 audit that must be tracked as open items, not forgotten.

**Gap 1: `orders/paid` topic absent before 2026-03-07.** Orders that converted from draft-order via the paid path before that date are not represented as `orders/create` events in tracking. They may or may not be covered by backfill. Any product with significant draft-order volume in that window has unknown presale capture completeness.

**Gap 2: 42 products have zero live event coverage.** Their entire positive ledger history is `orders/create_backfill` only. These products cannot have any live-derived presale figure. They are identified in the Phase 1 Block 2D query results.

**Gap 3: `presale_commitment_total` in `lifecycle_snapshot` was computed using a buggy topic filter** (`reconciliation/adjustment` with a slash instead of `reconciliation.adjustment` with a dot). This bug has been fixed in `lifecycle_snapshotter.py`. Existing snapshot rows were created with the old logic and may have slightly incorrect `presale_commitment_total` values for products that had pre-pub-date adjustments. The affected products are the subset of the 95 adjusted products whose adjustment `occurred_at` is before their `effective_pub_date`. In practice, all 95 adjustments were applied on 2026-03-31, which is after all 83 pub dates in the snapshot, so the existing `presale_commitment_total` values are unaffected. The fix matters for future snapshots only.

**Gap 4: Reconciliation script excludes backfill from open-commitment calculation.** This is intentional but must be documented. `fetch_ledger_open_qty` counts only `orders/create`, `orders/fulfilled`, `refunds/create`. It does not count `orders/create_backfill`. This means for any product where backfill is the primary positive source, the reconciliation script's open-commitment figure is systematically lower than the total ledger commitment. The three negative-balance products were a direct consequence of this.

**Gap 5: `ledger_reconciliation` cron status unknown.** The reconciliation log shows it was running multiple times per day on 2026-03-31. It is unclear whether it is currently scheduled as a Railway cron job. If it is, it must be disabled immediately. A second execution after the adjustments have been applied will compute delta of zero for all products and insert no new rows — but only if the idempotency guard works correctly. The risk is low but the cron should be explicitly disabled rather than relied upon to be a no-op.

---

### Rules for Phase 3

Phase 3 builds the post-cutover operational metrics path — a clean reporting layer using only Tier 1 data for active preorders. Before any code is written for Phase 3, the following constraints apply:

Every query that produces a presale quantity must include a `WHERE` clause or `CASE` expression that excludes `orders/create_backfill` and `reconciliation.adjustment` rows, or must explicitly label the output as `confidence: estimated`.

Every endpoint or view that surfaces a commitment figure must include a `data_confidence` field in its output. The value must be one of `verified`, `estimated`, or `forced_balance`. No figure may be surfaced without one of these labels.

The cutover date `2026-02-11` must be a named constant in the codebase, not a hardcoded string in individual queries.

The `lifecycle_snapshot` table must not be used as a reporting source for presale totals until a separate verification pass confirms the `presale_commitment_total` values are defensible. It may continue to be used for lifecycle closure state only.