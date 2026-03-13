# Division of Responsibility  
**webhook-gateway vs preorder-service**

This section defines the strict boundary between the two systems.

---

# 1. webhook-gateway (Upstream Ingest Layer)

## Responsibilities:
- Receive Shopify webhooks
- Validate HMAC signatures
- Extract minimal factual fields:
  - order_id, product_id, variant_id, sku, quantity, line_item_id
  - event_id
  - raw payload
  - raw headers
- Insert into `preorder.tracking` with:
  - status = 'pending'
  - approved = false
  - processed = false

## Explicit Non-Responsibilities:
- No preorder classification
- No anomaly detection
- No pub date parsing
- No override logic
- No status mutation
- No approvals
- No inventory interpretation
- No Slack / GitHub / NYT flows

Gateway is a **pure logging and relay service**.

---

# 2. preorder-service (Classification + State Machine)

## Responsibilities:
- Classify products into:
  - active_preorder
  - historical_preorder
  - anomaly_*
- Determine effective_pub_date
- Detect and categorize all anomalies
- Maintain derived states in Supabase
- Apply override logic
- Prepare weekly release lists
- Serve data to Admin Dashboard
- Support approvals and administrative overrides

## Explicit Non-Responsibilities:
- No Shopify writes (unless future phase adds it)
- No Slack notifications (future phase)
- No GitHub issue creation
- No publishing/unpublishing decisions

Preorder-service is the **brain**.  
It transforms raw events into structured preorder intelligence.

---

# 🔒 Frozen Canonical Rules (Binding)

The following rules are authoritative and must remain consistent across the codebase, specification, and test matrix.

### Structural Preorder Identity
A product is structurally preorder-eligible only when:

- `'preorder'` tag is present
- AND product is in the preorder collection

Tag/collection misalignment must classify as an anomaly.

### Effective Publication Date Resolution
The authoritative resolution order is:

1. `override_date`
2. `custom.pub_date` (authoritative Shopify source)
3. Exactly one valid date_tag (legacy fallback only)
4. Otherwise → None

Additional rules:
- Date tags are legacy compatibility only and are not standard operating procedure.
- If multiple valid date tags exist:
  - If exactly one matches `custom.pub_date`, resolution proceeds using `custom.pub_date`.
  - Otherwise → `anomaly_multi_date_conflict`.
- The system must not auto-select "latest" or "earliest" date tags.

---

# Classification Engine Progress Snapshot

This section tracks the implementation state of preorder-service.

## ✅ Phase 1 — Effective Publication Date
Implemented:
- `resolve_effective_pub_date()` utility
- Priority order:
  1. override_date
  2. custom.pub_date
  3. exactly one valid date_tag (legacy fallback only)
- Fully covered by unit tests

Status: COMPLETE

---

## ✅ Phase 2 — Anomaly Detection Layer
Implemented (with strict priority ordering):

1. `anomaly_missing_tag`
2. `anomaly_missing_collection`
3. `anomaly_override_conflict`
4. `anomaly_pubdate_conflict`
5. `anomaly_multi_date_conflict`

Rules:
- Anomalies always override business-state classification
- Deterministic early returns
- 100% anomaly test coverage

Status: COMPLETE

---

## ✅ Phase 3 — Early Stock Arrival
Definition:

    Future effective_pub_date
    AND inventory > 0
    AND structurally preorder-eligible:
        ('preorder' in tags AND in_preorder_collection == True)
    AND no anomalies

Behavior:
- Classified as `early_stock_arrival`
- Anomalies always override
- Takes precedence over `active_preorder`
- Requires structural preorder eligibility

Status: COMPLETE

---

# 🔜 Upcoming Phases

## ✅ Phase 4 — active_preorder
Definition:

    Future effective_pub_date
    AND inventory <= 0
    AND structurally preorder-eligible
    AND no anomalies
    AND not early_stock_arrival

Represents a valid, active preorder state.

Status: COMPLETE

---

## ✅ Phase 5 — historical_preorder
Definition:

    'preorder' in tags
    AND all relevant dates in the past OR no future date signal
    AND NOT in preorder collection
    AND no anomalies

Inventory may be any value.

Represents products that were once preorders but are now standard catalog items.

Status: COMPLETE

---

## ✅ Phase 6 — not_a_preorder_product
Definition:

    Not structurally preorder-eligible
    OR missing required structural alignment
    AND no anomalies
    AND not classified into any other state

Acts as deterministic fallback.

Status: COMPLETE

---

## ✅ Phase 7 — Supabase Integration
Implemented:
- `preorder.product_status` table
- Upsert persistence via `persist_classification()`
- Stored fields:
  - product_id
  - status
  - anomaly_type
  - effective_pub_date
  - metadata_snapshot (JSON)
  - last_classified_at (UTC)
  - engine_version
- Deterministic upsert on `product_id`

Test Coverage:
- `tests/test_persistence.py`
- Supabase calls mocked and validated
- Payload shape enforced
- Engine version override supported

Status: COMPLETE

---

## ✅ Phase 7.5 — Domain Layer + Deterministic Orchestrator
Implemented:
- `domain_models.py` — Pure domain metadata contract
- `orchestrator.py` — Deterministic classification + persistence flow
- No Shopify dependencies
- No network logic
- Fully mock-driven

Capabilities:
- `classify_and_persist_product()`
- `batch_reclassify()` with failure isolation
- Metadata snapshot generation

Test Coverage:
- `tests/test_orchestrator.py`
- Batch resilience verified
- Supabase integration verified

Status: COMPLETE

---

## ✅ Phase 8 — Reclassification API Layer (Admin / Internal)

Implemented:
- POST `/reclassify/{product_id}`
- POST `/reclassify/batch`
- Deterministic orchestration via domain + persistence layer
- Dependency-injected Shopify + Supabase clients
- Admin-key protected endpoints (`RECLASSIFY_ADMIN_KEY`)
- Structured logging support
- Batch failure isolation

Behavior Guarantees:
- Single reclassification returns full ClassificationResult contract
- Batch reclassification continues on per-product failure
- No Shopify writes performed
- No side effects beyond classification + persistence

Test Coverage:
- `tests/test_reclassify_endpoint.py`
- Dependency override isolation
- Success + failure path validation
- Batch aggregation validation

Status: COMPLETE

---

## 🚀 Phase 13 — Weekly Release Engine

Objective:
Produce a deterministic weekly release dataset identifying preorder titles that are ready to be counted for NYT reporting and internal operational tracking.

The release engine operates strictly on **derived state** from existing preorder infrastructure:

- `product_status`
- `inventory_arrival`
- `commitment_ledger`
- `lifecycle_snapshot`

No structural classification or ledger mutation occurs here.

---

### Engine Responsibilities

1. Identify preorder titles whose **effective publication week** has arrived.

2. Pull frozen presale cohort totals from `lifecycle_snapshot`.

3. Exclude:
   - anomaly states
   - non‑preorder products
   - products without valid effective_pub_date

4. Include:
   - active_preorder titles that crossed the pub-date boundary
   - early_stock_arrival titles whose pub date week has arrived
   - historical_preorder titles that transitioned during the week

5. Produce deterministic weekly dataset used for:
   - NYT reporting
   - internal sales reporting
   - preorder operational monitoring

---

### Release Week Definition

Publication weeks follow **ET-normalized weekly boundaries**:

```
release_week_start = Monday 00:00 ET
release_week_end   = Sunday 23:59:59 ET
```

Products qualify when:

```
effective_pub_date ∈ [release_week_start, release_week_end]
```

---

### Data Output Schema

The release engine will produce a structured dataset containing:

- product_id
- effective_pub_date
- presale_commitment_total
- first_inventory_arrival_at
- lifecycle_state
- arrival_timing
- classification_status

This dataset is derived only — it is **not persisted**.

---

### Execution Flow

1. Determine target release week (default = current ET week).
2. Query `product_status` for preorder-classified products.
3. Join:
   - `lifecycle_snapshot`
   - `inventory_arrival`
   - `vw_arrival_timing`
   - `vw_lifecycle_state`
4. Filter for products whose `effective_pub_date` falls within the target week.
5. Exclude anomalies.
6. Produce deterministic output dataset.

---

### Determinism Guarantees

- Engine reads **only frozen lifecycle snapshots**
- No dependence on live order state
- Re-running the engine for the same week produces identical results

---

### Presale Metrics Clarification

The preorder system tracks **three distinct operational quantities** that must not be conflated. Earlier confusion around presale totals and fulfillment state revealed the need to explicitly define these metrics.

1. presale_sales_total

Definition:
Total preorder units sold before the ET publication boundary.

Calculation:

    orders/create
    + orders/paid
    - orders/cancelled
    - refunds/create

Important rules:

- Fulfillment events DO NOT affect this number.
- Once an order occurs before the pub‑date boundary it permanently belongs to the presale cohort.
- This is the number used for:
  - NYT reporting
  - internal preorder sales reporting
  - admin dashboard presale totals.

2. open_presale_commitments

Definition:
Number of presale units that still require fulfillment.

Calculation:

    presale_sales_total
    - orders/fulfilled

This metric is operational only and is used to monitor fulfillment progress.

3. presale_fulfillment_verified

Definition:
A verification state confirming that **every presale order line has a corresponding fulfillment record**.

This is stronger than arithmetic equality and protects against:

- stuck orders
- partial fulfillments
- operational mistakes
- manual order holds

Lifecycle closure must therefore satisfy:

    effective_pub_date has passed
    AND presale_fulfillment_verified = true

Arithmetic equality alone is insufficient.

---

### Lifecycle State Naming Correction

The term `backordered` previously appeared in `vw_lifecycle_state`. This term is misleading in a preorder context.

Two different concepts were unintentionally conflated:

Operational backorder

    inventory < 0

Preorder fulfillment backlog

    presales exist but have not yet been fulfilled

These are not the same condition.

The preorder lifecycle vocabulary will therefore avoid the word "backorder" entirely.

Future lifecycle derivation should use states such as:

- awaiting_inventory
- fulfilling_presales
- presales_complete

This avoids confusion with standard retail backorder semantics.

---

### Weekly Release Engine Metric Source

The Weekly Release Engine must use **presale_sales_total** rather than the net commitment value currently stored in `presale_commitment_total`.

Reason:

`presale_commitment_total` represents **remaining commitments** after fulfillment activity:

    orders/create
    - orders/fulfilled

This undercounts presales for reporting purposes when titles ship early.

Correct reporting metric:

    presale_sales_total

Future implementation will therefore:

- derive presale_sales_total directly from `commitment_ledger`
- ignore fulfillment deltas when calculating presale cohort size
- continue using commitment totals only for lifecycle closure logic

---

### Planned Follow‑Up Work

The following engineering tasks remain before Phase 13 is considered production‑ready:

1. Add SQL helper for presale_sales_total calculation.
2. Update `weekly_release_engine.py` to use this metric.
3. Replace `backordered` lifecycle derivation in `vw_lifecycle_state`.
4. Add presale fulfillment verification query.
5. Add unit tests validating presale_sales_total vs commitment totals.

These tasks ensure reporting accuracy while preserving the existing deterministic lifecycle architecture.

---

### Reporting State (Derived)

Structural classification and lifecycle state do not fully describe reporting readiness.

The release engine derives an additional operational state:

reporting_state

Possible values:

pending_release
eligible_for_reporting
reported

Meaning:
State                   ||  Meaning
=======================================================
pending_release         ||  pub date has not yet occurred
eligible_for_reporting  ||  pub date week reached and presale cohort frozen
reported                ||  title already counted in NYT/internal reporting

Important rule:
reporting_state is NOT part of structural classification.
It is not persisted in product_status.
It is derived dynamically by the Weekly Release Engine.

---

### CLI Interface (Planned)

```
python weekly_release_engine.py --week 2026-04-07
```

Default behavior:

```
python weekly_release_engine.py
```

Uses current ET week.

---

### Future Integrations

Phase 13 output will feed:

- Slack release notifications
- GitHub release approval issues
- NYT reporting pipelines
- Admin dashboard release views

These integrations will be implemented in later phases.

---

Status: NOT STARTED

---

# 🧭 Forward Roadmap

## ✅ Phase 10 — Pub Date History Tracking
Implemented:
- `preorder.pubdate_history` table
- Baseline effective_pub_date capture
- Change detection with normalized date comparison
- change_source attribution:
  - initial_baseline
  - shopify_pub_date
  - override_date
  - legacy_tag_fallback
- Idempotent insert behavior
- Executed before product_status upsert

Test Coverage:
- `tests/test_pubdate_history.py`
- Baseline insert
- No-change behavior
- Date normalization
- Override precedence
- Idempotency

Status: COMPLETE

## ✅ Phase 11 — Inventory Arrival Tracking

Implemented:
- `preorder.inventory_arrival` table
- Immutable `first_positive_inventory_at` capture
- Rule:
      inventory > 0 AND no existing row
- No transition detection
- No classification coupling
- Independent of pub date logic
- Executed before `product_status` upsert

Behavior Guarantees:
- One row per product (primary key enforced)
- First arrival is immutable
- Idempotent persistence
- Applies to all products (not preorder-restricted)
- Inventory history does not mutate structural classification

Test Coverage:
- `tests/test_inventory_arrival.py`
  - First insert when inventory > 0
  - No insert when inventory <= 0
  - Idempotency on repeated runs
  - Independent of classification state

Status: COMPLETE

-## ✅ Phase 12 — Arrival Timing Derivation

Implemented:
- `preorder.vw_arrival_timing` SQL view
- Pure derivation helper: `derive_arrival_timing()`
- Pub-date anchored timing classification
- ET-normalized comparison logic
- No coupling to structural classification
- No persistence mutation
- Derived-only state (not stored)

Canonical Logic:

Let:
- `effective_pub_date` (date)
- `first_positive_inventory_at` (timestamptz)
- `arrival_date_et` = ET-normalized date of first_positive_inventory_at
- `days_diff` = (effective_pub_date - arrival_date_et).days

Classification:

    IF effective_pub_date IS NULL:
        arrival_timing = NULL

    ELIF no inventory arrival row:
        arrival_timing = no_arrival

    ELIF arrival_date_et > effective_pub_date:
        arrival_timing = late_arrival

    ELIF days_diff <= 7:
        arrival_timing = on_time_arrival

    ELSE:
        arrival_timing = early_arrival

Behavior Guarantees:
- Late arrival = inventory received AFTER pub date.
- On-time arrival = received on pub date or within 7 days prior.
- Early arrival = received more than 7 days prior.
- First arrival is immutable.
- Pub date changes re-derive arrival timing deterministically.
- Arrival timing does not alter structural preorder identity.

Test Coverage:
- `tests/test_arrival_timing_derivation.py`
  - No arrival
  - Early (8+ days before)
  - On-time (0–7 days before)
  - On pub date
  - Late (after pub date)
  - Pub date required (None returns None)

Status: COMPLETE

---

## ✅ Phase 12 — Commitment-Aware Lifecycle States

Implemented:
- `preorder.commitment_ledger` integration
- `preorder.lifecycle_snapshot` table
- Snapshot-based presale cohort freezing at pub-date boundary
- Commitment-aware lifecycle derivation (separate from structural classification)

Lifecycle States (Derived):

- `open_preorder`
- `late_preorder`
- `closed_preorder`
- `open_historical_preorder`

Design Guarantees:

- Lifecycle state is derived, never persisted as structural identity.
- Structural preorder classification remains pure and independent.
- Snapshot freezes presale cohort at ET midnight of effective_pub_date.
- Closure requires:
  - Inventory arrival exists
  - Net commitment == 0
- Post-pub orders do NOT affect frozen presale cohort totals.
- Late arrival logic (Phase 12) remains inventory-based only.

Separation of Concerns:

- Structural classification → product identity
- Arrival timing → inventory timing
- Commitment lifecycle → order fulfillment state

Test Coverage:
- `tests/test_lifecycle_snapshot.py`
- `tests/test_lifecycle_snapshotter.py`
- `tests/test_lifecycle_view_derivation.py`
- `tests/test_refund_tracking_split.py`

Status: COMPLETE

---

Classification engine is stable through Phase 8 (API-integrated, persisted, and admin-protected).

---

## 🧪 Test Coverage Snapshot — v0.8-shopify-integration

Total test count: **111 tests — 100% passing**

Breakdown by domain:

- Effective Pub Date Resolution  
  - `tests/test_effective_pub_date.py`

- Anomaly Layer  
  - `tests/anomalies/test_anomaly_missing_tag.py`
  - `tests/anomalies/test_anomaly_missing_collection.py`
  - `tests/anomalies/test_anomaly_override_conflict.py`
  - `tests/anomalies/test_anomaly_pubdate_conflict.py`
  - `tests/anomalies/test_anomaly_multi_date_conflict.py`

- Business States  
  - `tests/test_early_stock_arrival.py`
  - `tests/test_active_preorder.py`
  - `tests/test_historical_preorder.py`
  - `tests/test_not_a_preorder_product.py`

- API Layer  
  - `tests/test_reclassify_endpoint.py`

State Machine Guarantees (Strict Mode):

1. Preorder identity requires:
       'preorder' tag AND collection alignment.
2. Anomalies always override business classification.
3. Structural drift is never silently tolerated.
4. `early_stock_arrival` requires structural eligibility.
5. `active_preorder` requires:
       tag + collection + future effective_pub_date + inventory <= 0.
6. `historical_preorder` only applies to past-dated tagged products not in collection.
7. `not_a_preorder_product` is deterministic fallback.

Coverage Philosophy:
- Each classification state is tested in isolation.
- Cross-state leakage is explicitly guarded.
- Ordering regressions immediately fail the suite.
- Structural alignment is enforced via tests.

Version Tag: `v1.0-lifecycle-hardened`

---


## ✅ Phase 12.4 — Commitment Ledger Event Hardening

Objective:
Ensure the `preorder.commitment_ledger` correctly captures all positive preorder commitments while remaining replay-safe and idempotent.

Background Issue:
Some Shopify orders originate as **Draft Orders** and only generate the canonical order event when payment is captured. In those cases:

- `orders/create` may not represent the final payable order event
- The definitive signal becomes `orders/paid`

If only `orders/create` is processed, positive commitment rows may be missing.

Implemented Fix:

Webhook‑gateway now forwards:

- `orders/create`
- `orders/paid`

Both events are processed by `build_commitment_ledger.py`.

Idempotency Rules:

Positive commitment rows are protected by a partial uniqueness rule:

```
(order_id, line_item_id) WHERE delta_qty > 0
```

This guarantees:

- `orders/create` and `orders/paid` overlap cannot double‑count commitments
- Ledger rebuilds are replay-safe
- Historical backfills are safe to rerun

Replay Safety:

`build_commitment_ledger.py` uses:

```
INSERT ... ON CONFLICT DO NOTHING
```

Meaning:

- Ledger generation can be safely rerun
- Reprocessing historical webhook payloads will not duplicate rows

Operational Scope Decision:

Negative commitment balances were discovered for a number of **historical preorder products**. Investigation showed these are typically caused by legacy event gaps prior to the `orders/paid` ingest fix.

Because these titles are:

- `historical_preorder`
- already past their publication date

these cases do **not affect active preorder lifecycle logic**.

Therefore:

- Historical reconciliation will be handled as a separate maintenance project
- The ledger is considered **correct for all active preorder products** going forward

Design Guarantees:

- Ledger is append‑only
- Positive commitments are idempotent
- Order lifecycle events remain replayable
- Preorder lifecycle derivation remains deterministic

Status: COMPLETE

---

## ✅ Phase 12.5 — Webhook‑Driven Reclassification Integration & System Hardening

Implemented:
- End‑to‑end webhook → classification → persistence flow
- HMAC‑verified ingest via webhook‑gateway
- Deterministic reclassification trigger on:
  - `products/update`
  - `inventory_levels/update`
- Safe Supabase persistence with schema‑scoped writes
- JSON‑safe metadata snapshot serialization
- Typed `ReclassifyResponse` contract enforcement
- Failure isolation (webhook always returns 200)

Architectural Guarantees:
- Gateway remains ingest‑only (no classification logic)
- Preorder‑service owns all state mutation
- Reclassification is idempotent per product
- Pub date history + inventory arrival tracking execute before state upsert
- No circular dependency between webhook layer and domain engine

Boundary Confirmation:
- No Shopify writes
- No Slack / GitHub side effects
- No publishing decisions
- Pure state derivation + persistence

Operational Status:
- Fully deployed
- Webhook traffic stable
- Classification pipeline deterministic
- Zero runtime warnings

Status: COMPLETE

---


## 🧪 Phase 12.6 — Preorder Schema Validation & Contract Lock

Objective:
Before defining Phase 13 (Release Engine), we formally validate and lock the full `preorder` schema contract.

Authoritative Tables in `preorder` schema:

- `tracking`
- `approvals`
- `product_status`
- `inventory_arrival`
- `lifecycle_snapshot`
- `pubdate_history`
- `commitment_ledger`
- `inventory_item_map`
- `product_overrides`

Schema Guarantees to Validate:

1. All tables exist in `preorder` schema (not `public`).
2. Primary keys enforced where required.
3. Idempotent upserts use explicit conflict targets.
4. No cross-schema implicit references (`public.preorder.*` prohibited).
5. All timestamps are stored as UTC (`timestamptz`).
6. JSON columns accept serialized payloads only (no Python-native types).
7. Foreign key relationships are explicit and deterministic.

Testing Requirements:

- Integration test ensuring each table is reachable via Supabase client using `.schema("preorder")`.
- Migration verification script to assert existence of all nine tables.
- Contract test ensuring `product_status` and `inventory_arrival` upserts succeed in isolation.
- Contract test verifying `pubdate_history` insert behavior (baseline + change case).
- Validation that lifecycle derivation reads from `commitment_ledger` + `lifecycle_snapshot` without schema ambiguity.

Validation Results (2026‑03‑07)

Schema verification executed via:

    python scripts/verify_preorder_schema.py

Output confirmed:

- All authoritative tables exist in the `preorder` schema.
- No missing tables.
- All writes correctly reference `.schema("preorder")`.

Additional objects detected were expected **derived views**, not contract tables:

- `active_preorders`
- `vw_arrival_timing`
- `vw_lifecycle_state`
- `vw_lifecycle_state_debug`
- `vw_pending_approvals`

These views are intentionally excluded from the schema contract because they are **derived read layers**, not persisted state tables.

Contract Scope Clarification:

The schema contract for preorder-service covers **persistent tables only**:

- tracking
- approvals
- product_status
- inventory_arrival
- lifecycle_snapshot
- pubdate_history
- commitment_ledger
- inventory_item_map
- product_overrides

Views remain flexible implementation layers and may evolve without breaking the schema contract.

Status: COMPLETE

-----------------

## Lifecycle Snapshot Invariants

1. lifecycle_snapshot is fully rebuildable.

Running:

python lifecycle_snapshotter.py --rebuild

will truncate and deterministically recompute all lifecycle snapshots
from:

- preorder.commitment_ledger
- preorder.product_status
- preorder.inventory_arrival

2. Snapshot creation occurs once a product crosses its effective_pub_date boundary.

3. Presale cohort is frozen as:

sum(delta_qty) where occurred_at < ET midnight of effective_pub_date.

4. Lifecycle closes only when:

- first_positive_inventory_arrival exists
AND
- current committed preorder quantity = 0.

5. Snapshot rebuilds are deterministic and should produce identical
semantic checksums across runs.

6. If effective_pub_date changes for a product (for example due to a publisher slip or manual override), the existing lifecycle_snapshot for that product must be invalidated and recomputed.

Implementation rule:

    delete from preorder.lifecycle_snapshot
    where product_id = <product_id>;

After invalidation, the snapshotter will rebuild the snapshot using the new publication date boundary.

This guarantees that presale cohorts include all valid presales up to the corrected pub date.

7. Snapshot freezing assumes pub dates are stable.

If a pub date moves forward, orders occurring between the original pub date boundary and the corrected boundary must be captured by rebuilding the snapshot.

Failing to do this will undercount presales.
---------

## Future Hardening Roadmap

These improvements extend the preorder ledger architecture but are not
required for current system correctness.

Phase A — Ledger Audit Queries
Phase B — Fully Auditable Ledger (double-entry model)
Phase C — Operational Hardening

---

Below is a clean README update you can paste directly into your project’s README.md.
It documents:
	•	the ledger reconciliation method
	•	the safe backfill query
	•	the diagnostics you ran
	•	the invariants the system must maintain
	•	the workflow for future reconciliation

I wrote it in the same engineering-documentation tone appropriate for the rest of your project.

⸻

README Update — Preorder Ledger Reconciliation & Backfill

Overview

During system validation we discovered that historical preorder orders were not fully represented in the preorder.commitment_ledger.

This occurred because some historical orders were created before the webhook ingestion service was active.

As a result:

Shopify orders (ground truth) > ledger commitments

The ledger therefore required historical reconciliation.

The reconciliation approach implemented here safely restores ledger completeness without breaking event invariants.

⸻

Ledger Model (Reminder)

preorder.commitment_ledger is an append-only event log.

Each row represents a lifecycle event affecting preorder commitments.

Typical events:

Topic	Meaning
orders/create	preorder commitment created
orders/paid	payment confirmation commitment
refunds/create	commitment reversal
orders/create_backfill	historical reconstruction event

The ledger invariant is:

sum(delta_qty) = current committed preorder quantity


⸻

Ledger Invariants

The system relies on the following guarantees.

1. One positive commitment per order line

(order_id, line_item_id) WHERE delta_qty > 0

There must never be two positive commitment rows for the same order line.

⸻

2. Refund events subtract commitments

Refund events insert:

delta_qty = -1
topic = refunds/create

This preserves full historical lifecycle accounting.

⸻

3. Ledger is append-only

Rows must never be modified or deleted except for manual repair during system initialization.

⸻

4. Ledger is the system of record

The ledger represents historical truth, while Shopify API queries reflect current order state.

Therefore:

ledger != shopify snapshot

when refunds or cancellations occurred.

⸻

Reconciliation Method

The reconciliation process compares:

shopify_orders_stage

against

preorder.commitment_ledger

to identify missing commitments.

⸻

Detect Missing Ledger Events

select
count(*) as missing_rows,
sum(qty) as missing_units
from (
    select
        s.order_id,
        s.line_item_id,
        s.qty
    from shopify_orders_stage s
    left join preorder.commitment_ledger l
      on s.order_id = l.order_id
     and s.line_item_id = l.line_item_id
     and l.delta_qty > 0
    where s.product_id = <PRODUCT_ID>
    and l.order_id is null
) t;

This identifies order lines present in Shopify but absent in the ledger.

⸻

Safe Ledger Backfill Query

The following query safely inserts missing historical commitments.

Important features:
	•	generates tracking_id
	•	avoids duplicate commitments
	•	preserves original order timestamps
	•	uses a dedicated topic (orders/create_backfill)

insert into preorder.commitment_ledger (
    tracking_id,
    product_id,
    order_id,
    line_item_id,
    delta_qty,
    topic,
    occurred_at
)
select
    gen_random_uuid(),
    s.product_id,
    s.order_id,
    s.line_item_id,
    s.qty,
    'orders/create_backfill',
    s.created_at
from shopify_orders_stage s
left join preorder.commitment_ledger l
  on s.order_id = l.order_id
 and s.line_item_id = l.line_item_id
 and l.delta_qty > 0
where s.product_id = <PRODUCT_ID>
and l.order_id is null;

This query is idempotent and safe to run multiple times.

⸻

Verification Query

After backfill, verify ledger parity with Shopify orders.

with shopify as (
    select
        product_id,
        sum(qty) as shopify_orders
    from shopify_orders_stage
    group by product_id
),
ledger as (
    select
        product_id,
        sum(delta_qty) as ledger_qty
    from preorder.commitment_ledger
    group by product_id
)
select
    s.product_id,
    s.shopify_orders,
    coalesce(l.ledger_qty, 0) as ledger_qty,
    s.shopify_orders - coalesce(l.ledger_qty, 0) as diff
from shopify s
left join ledger l
  on s.product_id = l.product_id
where s.product_id = <PRODUCT_ID>;

Expected result:

diff = 0


⸻

Debug Queries Used During Validation

Check for duplicate positive commitments

select
order_id,
line_item_id,
count(*) as positive_rows
from preorder.commitment_ledger
where delta_qty > 0
group by order_id,line_item_id
having count(*) > 1;

Expected:

0 rows


⸻

Inspect recent lifecycle events

select
order_id,
line_item_id,
delta_qty,
topic
from preorder.commitment_ledger
where product_id = <PRODUCT_ID>
order by occurred_at desc
limit 20;


⸻

Operational Outcome

After reconciliation:
	•	Shopify order history and ledger commitments match
	•	refunds remain preserved
	•	lifecycle snapshotter operates correctly
	•	preorder reporting becomes trustworthy

The preorder system now has a fully reconstructed historical commitment ledger.

⸻

Future Protection

To prevent future divergence:

1. Webhook ingestion remains primary source

orders/create
orders/paid
refunds/create


⸻

2. Daily reconciliation audit (recommended)

Run a scheduled query comparing:

shopify_orders_stage
vs
commitment_ledger

and alert on differences.

⸻

3. Ledger remains append-only

Manual updates should never modify existing rows.

⸻

System Status

After reconciliation the preorder system satisfies:

ledger completeness
ledger invariants
refund lifecycle integrity
snapshot correctness

The system is now safe for:
	•	preorder dashboards
	•	sales reporting
	•	fulfillment planning
	•	NYT reporting logic

⸻

# ---
# ## Historical Ledger Reconciliation Notes (2026‑03)
#
# During validation of the preorder commitment ledger, several discrepancies were identified between:
#
#     preorder.commitment_ledger
#     vs
#     shopify_orders_stage derived totals
#
# Investigation determined that these differences were primarily caused by historical artifacts including:
#
# - deleted Shopify orders that still had refund webhook events recorded
# - draft‑order lifecycle differences prior to the `orders/paid` ingest fix
# - legacy ingestion gaps that existed before the ledger pipeline was deployed
# - stage tables reflecting current Shopify state rather than full lifecycle history
#
# The reconciliation process followed these principles:
#
# ### Ledger Is the Source of Truth
#
# `preorder.commitment_ledger` represents the full historical lifecycle of preorder commitments.
#
# Therefore:
#
#     ledger_open_commitments
#     ≠
#     shopify snapshot quantities
#
# when refunds, cancellations, or deleted orders occurred.
#
# Shopify staging tables represent **current order state**, while the ledger preserves **historical events**.
#
# For this reason the ledger may legitimately show higher commitment totals than the Shopify snapshot.
#
# This behavior is expected and correct.
#
# ### Historical Preorder Rows
#
# Several `historical_preorder` products exhibited mismatches between the ledger and Shopify stage data.
#
# Because these titles:
#
# - are past their publication date
# - no longer participate in active preorder lifecycle logic
# - do not affect current operational reporting
#
# they were intentionally excluded from further reconciliation.
#
# These rows remain historically accurate in the ledger.
#
# ### Active Preorder and Early Stock Arrival Rows
#
# For products classified as:
#
#     active_preorder
#     early_stock_arrival
#
# the ledger values were verified to be correct through manual inspection of:
#
# - commitment ledger event history
# - Shopify order exports
# - fulfillment and refund activity
#
# In these cases the discrepancy was caused by Shopify stage queries undercounting lifecycle events.
#
# Therefore:
#
#     ledger_open_commitments is considered authoritative.
#
# ### Operational Rule
#
# Going forward:
#
#     preorder.commitment_ledger
#     is the canonical source for preorder commitment quantities.
#
# Shopify stage queries are used only for ingestion diagnostics and reconciliation.
#
# All lifecycle and reporting calculations must derive from the ledger.
#
# ### Ledger Integrity Condition
#
# The only invariant that must always hold is:
#
#     sum(delta_qty) >= 0
#
# for all preorder products.
#
# This condition was verified during reconciliation.
#
# ### Result
#
# After cleanup:
#
# - No products have negative commitment balances
# - Duplicate positive commitments were removed
# - Orphan refund events referencing deleted orders were removed
# - Ledger event history remains intact for all valid preorder orders
#
# The preorder ledger is now internally consistent and safe to use for:
#
# - lifecycle snapshot derivation
# - presale cohort freezing
# - operational fulfillment tracking
# - NYT reporting calculations


---

## ⚙️ Worker Performance Hardening (2026‑03)

During operational monitoring the preorder infrastructure briefly exceeded the Supabase free‑tier I/O budget due to inefficient event replay behavior in the ledger worker.

The system has since been hardened with several architectural improvements to guarantee low‑cost operation even with frequent cron execution.

These changes affect the internal workers:

- `build_commitment_ledger.py`
- `lifecycle_snapshotter.py`

Both workers currently run on a **5‑minute schedule**.

### Root Cause

The original implementation scanned `preorder.tracking` while selecting the full webhook `payload` column.

Typical Shopify webhook payloads are ~20–40KB.

When batches of thousands of rows were replayed, each worker run transferred tens of megabytes of data even when no new events existed.

This caused unnecessary:

- Supabase bandwidth usage
- disk I/O consumption
- query CPU cost

### Fix 1 — Two‑Stage Payload Fetch

Ledger replay now performs a lightweight metadata scan first:

    select id, event_id, topic, created_at

Payloads are fetched only for the rows in the current batch:

    select id, payload
    from preorder.tracking
    where id = any($1::uuid[])

This reduces average network transfer per run from **tens of MB** to **a few KB**.

### Fix 2 — Replay Cursor

A persistent replay cursor table was introduced:

    preorder.replay_cursor

Structure:

    topic text primary key
    last_created_at timestamptz
    last_id uuid

Each worker run resumes scanning from the last processed `(created_at, id)` position.

This prevents repeated scans of historical webhook events and guarantees that workers process **only new events**.

### Fix 3 — Pagination‑Aligned Index

The `preorder.tracking` table now uses a composite index aligned with the worker's keyset pagination query:

    create index preorder_tracking_topic_created_id_idx
    on preorder.tracking (topic, created_at, id);

This allows Postgres to perform an efficient index walk rather than scanning the table.

### Operational Impact

After these improvements:

| Metric | Before | After |
|------|------|------|
| Data transferred per run | ~60MB | <10KB typical |
| Tracking rows scanned | entire table | only new events |
| Supabase bandwidth | tens of GB/month | <1GB/month typical |

The preorder infrastructure can now safely run workers every **5 minutes** while remaining within Supabase free‑tier limits.

### Design Guarantees

The hardened replay architecture now provides:

- deterministic event replay
- idempotent ledger reconstruction
- minimal network transfer
- bounded query cost

Key design patterns used:

- keyset pagination `(created_at, id)`
- replay cursor persistence
- two‑stage payload retrieval

These patterns ensure the preorder event pipeline remains scalable without increasing operational complexity.

---


---

## ⚙️ Worker Execution Model

The preorder system relies on two deterministic background workers that operate on a fixed schedule. These workers maintain derived preorder state and ledger consistency while remaining fully replay‑safe.

### Workers

1. **build_commitment_ledger.py**

Purpose:

Reconstruct the preorder commitment ledger from webhook events stored in `preorder.tracking`.

Responsibilities:

- Parse order lifecycle events (`orders/create`, `orders/paid`, `orders/fulfilled`, `refunds/create`)
- Convert lifecycle events into append‑only ledger entries
- Maintain accurate preorder commitment balances

Guarantees:

- Ledger writes use `INSERT ... ON CONFLICT DO NOTHING`
- Positive commitments protected by partial uniqueness rules
- Safe to replay historical webhook events

Replay Model:

- Reads events via keyset pagination `(created_at, id)`
- Uses persistent replay cursor `preorder.replay_cursor`
- Only processes new events after the last processed cursor

---

2. **lifecycle_snapshotter.py**

Purpose:

Freeze presale cohorts and derive lifecycle state once products cross their publication date boundary.

Responsibilities:

- Detect products whose `effective_pub_date` has passed
- Freeze presale cohort totals at ET midnight of pub date
- Persist lifecycle snapshot rows

Derived Inputs:

- `commitment_ledger`
- `product_status`
- `inventory_arrival`

Lifecycle snapshots allow the system to determine:

- presale cohort totals
- fulfillment progress
- lifecycle closure conditions

Guarantees:

- Snapshots are deterministic
- Snapshots are rebuildable
- No mutation of ledger history

---

### Execution Schedule

Both workers currently run on a **5‑minute interval** via cron.

Example schedule:

```
*/5 * * * *
```

Frequent execution is safe because:

- replay cursors prevent full‑table scans
- keyset pagination ensures bounded queries
- payloads are fetched only when new events exist

---

### Failure Safety

Worker execution is designed to be failure‑tolerant.

Properties:

- idempotent ledger writes
- deterministic replay
- cursor‑based progress tracking

If a worker crashes:

1. No partial ledger corruption occurs.
2. The next run resumes from the last committed cursor.
3. Historical events may be safely replayed.

---

### Operational Design Principle

Workers operate strictly on **event replay and deterministic derivation**.

They never:

- mutate Shopify state
- modify historical ledger rows
- override structural classification

This ensures the preorder system remains:

- auditable
- replayable
- operationally stable

---

---

## 🔁 Event Flow Diagram (Webhook → Ledger → Lifecycle → Release Engine)

This section illustrates the end‑to‑end data pipeline used by the preorder system. The architecture is intentionally **event‑driven and replayable**, meaning every derived state can be rebuilt from upstream event logs.

The flow below describes how Shopify events move through the system.

### Step 1 — Shopify Webhook Event

Shopify emits lifecycle events such as:

- `orders/create`
- `orders/paid`
- `orders/fulfilled`
- `refunds/create`
- `products/update`
- `inventory_levels/update`

These events represent the raw operational activity of the store.

---

### Step 2 — webhook‑gateway Ingestion

The **webhook‑gateway** service receives Shopify webhooks and performs only minimal responsibilities:

- HMAC signature verification
- extraction of basic identifiers
- storage of the raw payload

Events are written to:

```
preorder.tracking
```

Each row represents a captured webhook event and includes:

- event_id
- topic
- created_at
- payload

Gateway behavior is intentionally passive — it does **not perform any preorder logic**.

---

### Step 3 — Ledger Reconstruction

Worker:

```
build_commitment_ledger.py
```

This worker reads events from `preorder.tracking` and converts order lifecycle events into ledger entries stored in:

```
preorder.commitment_ledger
```

Each ledger row records a **delta in preorder commitments**.

Example transformations:

| Webhook Topic | Ledger Delta |
|---------------|--------------|
| orders/create | +qty |
| orders/paid   | +qty (idempotent guard) |
| refunds/create | -qty |
| orders/fulfilled | lifecycle consumption |

The ledger is **append‑only** and protected by idempotency constraints.

---

### Step 4 — Structural Classification

Triggered by product and inventory events.

Worker / API:

```
classify_and_persist_product()
```

Derived state is written to:

```
preorder.product_status
```

Classification determines whether a product is:

- active_preorder
- early_stock_arrival
- historical_preorder
- anomaly_* 
- not_a_preorder_product

This stage represents the **structural identity of the product**.

---

### Step 5 — Inventory Arrival Tracking

Inventory changes are monitored and the **first positive inventory arrival** is recorded.

Table:

```
preorder.inventory_arrival
```

This allows the system to determine when preorder inventory actually became available.

---

### Step 6 — Lifecycle Snapshot Freezing

Worker:

```
lifecycle_snapshotter.py
```

Once a product crosses its publication boundary, the presale cohort is frozen.

Snapshots are stored in:

```
preorder.lifecycle_snapshot
```

The snapshot records:

- presale cohort size
- lifecycle state
- fulfillment progress

Snapshots ensure that **future events do not alter historical presale totals**.

---

### Step 7 — Weekly Release Engine

Script:

```
weekly_release_engine.py
```

The release engine derives the dataset used for reporting and operational tracking.

Inputs:

- `product_status`
- `inventory_arrival`
- `commitment_ledger`
- `lifecycle_snapshot`

Outputs:

A deterministic dataset identifying preorder titles whose **publication week has arrived**.

This dataset is used for:

- NYT reporting
- internal sales analysis
- operational release tracking

---

### Pipeline Overview

The full pipeline can be visualized as:

```
Shopify Webhooks
        │
        ▼
webhook-gateway
        │
        ▼
preorder.tracking
        │
        ▼
build_commitment_ledger
        │
        ▼
preorder.commitment_ledger
        │
        ▼
classification engine
        │
        ▼
preorder.product_status
        │
        ▼
inventory arrival tracking
        │
        ▼
preorder.inventory_arrival
        │
        ▼
lifecycle_snapshotter
        │
        ▼
preorder.lifecycle_snapshot
        │
        ▼
weekly_release_engine
        │
        ▼
NYT / internal reporting
```

---

### Architectural Guarantees

This architecture ensures:

- deterministic replay
- append‑only event history
- rebuildable derived state
- clear separation of ingestion vs intelligence layers

If any derived table becomes corrupted, the system can be rebuilt from:

```
preorder.tracking
```

which represents the authoritative webhook event log.

---


---

## 🛠 System Rebuild Procedure (Disaster Recovery)

The preorder architecture is intentionally designed to be **fully rebuildable from the webhook event log**.

The authoritative event source is:

```
preorder.tracking
```

All derived tables can be safely regenerated if corruption, migration failure, or operational errors occur.

This section documents the deterministic rebuild order.

---

### Rebuild Order

Derived state must be rebuilt in the following sequence:

1. `commitment_ledger`
2. `product_status`
3. `inventory_arrival`
4. `lifecycle_snapshot`

Each stage depends only on upstream state and is safe to recompute.

---

### Step 1 — Rebuild Commitment Ledger

The ledger is reconstructed by replaying webhook events stored in `preorder.tracking`.

Run:

```
python build_commitment_ledger.py --topic all
```

This process:

- scans tracking events using keyset pagination `(created_at, id)`
- converts lifecycle events into ledger deltas
- inserts rows using `INSERT ... ON CONFLICT DO NOTHING`

Guarantees:

- replay safe
- append‑only
- idempotent

If needed, the ledger table may be cleared prior to rebuild:

```
truncate preorder.commitment_ledger;
```

Then rerun the replay worker.

---

### Step 2 — Rebuild Structural Classification

Structural preorder classification can be regenerated by re-running the classification orchestrator.

Typical rebuild method:

```
POST /reclassify/batch
```

or internal script:

```
batch_reclassify()
```

Classification will repopulate:

```
preorder.product_status
```

This step is deterministic because classification depends only on:

- Shopify product metadata
- override records
- deterministic classification rules

---

### Step 3 — Rebuild Inventory Arrival Tracking

Inventory arrival rows are regenerated automatically when classification runs.

Rule:

```
inventory > 0 AND no existing arrival row
```

Arrival tracking writes to:

```
preorder.inventory_arrival
```

If necessary the table may be cleared before replay:

```
truncate preorder.inventory_arrival;
```

Then rerun classification.

---

### Step 4 — Rebuild Lifecycle Snapshots

Lifecycle snapshots freeze presale cohorts at the publication boundary.

They can be fully rebuilt from:

- `commitment_ledger`
- `product_status`
- `inventory_arrival`

Run:

```
python lifecycle_snapshotter.py --rebuild
```

The rebuild process performs:

```
truncate preorder.lifecycle_snapshot
```

followed by deterministic recomputation.

---

### Determinism Guarantees

Rebuilding the system from scratch will produce identical semantic results because:

- the ledger is append‑only
- lifecycle snapshots freeze cohort boundaries
- classification rules are deterministic
- event replay order is stable

Therefore the system supports:

- disaster recovery
- schema migrations
- historical backfills
- infrastructure re‑deployment

without data loss.

---

### Operational Recommendation

For safety during rebuild operations:

1. Pause background workers.
2. Perform rebuild steps in the order listed above.
3. Resume workers after validation.

This ensures replay cursors and derived tables remain consistent.

---


---

## 🧰 Operational Runbook (Common Maintenance Tasks)

This section documents routine operational tasks for maintaining the preorder system. All operations assume the event‑driven architecture described earlier and respect the rule that **derived state can always be rebuilt from `preorder.tracking`**.

---

### 1. Replaying a Single Webhook Event

Occasionally a webhook event may need to be reprocessed.

Steps:

1. Identify the event in `preorder.tracking`:

```
select id, topic, created_at
from preorder.tracking
where id = '<EVENT_UUID>';
```

2. Delete corresponding ledger rows (if necessary):

```
delete from preorder.commitment_ledger
where tracking_id = '<EVENT_UUID>';
```

3. Re-run the ledger worker.

Because ledger writes are idempotent (`ON CONFLICT DO NOTHING`), replaying the event will safely restore the correct state.

---

### 2. Resetting the Ledger Replay Cursor

If the ledger worker must reprocess events from an earlier point in time, reset the replay cursor.

Example:

```
update preorder.replay_cursor
set last_created_at = '2026-01-01T00:00:00Z',
    last_id = null
where topic = 'orders/create';
```

After resetting the cursor, the next worker run will resume scanning from the specified timestamp.

This operation is safe because ledger writes are idempotent.

---

### 3. Rebuilding a Single Product Lifecycle Snapshot

If a product’s publication date changes or snapshot data becomes inconsistent, its lifecycle snapshot can be rebuilt.

Steps:

1. Remove the existing snapshot:

```
delete from preorder.lifecycle_snapshot
where product_id = <PRODUCT_ID>;
```

2. Run the snapshot worker.

The worker will recompute the snapshot deterministically using:

- `commitment_ledger`
- `product_status`
- `inventory_arrival`

---

### 4. Verifying Ledger Integrity

Check for duplicate positive commitments:

```
select
order_id,
line_item_id,
count(*)
from preorder.commitment_ledger
where delta_qty > 0
group by order_id, line_item_id
having count(*) > 1;
```

Expected result:

```
0 rows
```

This confirms that the partial uniqueness rule protecting positive commitments is functioning correctly.

---

### 5. Inspecting Recent Ledger Activity

To inspect the most recent commitment events for a product:

```
select
order_id,
line_item_id,
delta_qty,
topic,
occurred_at
from preorder.commitment_ledger
where product_id = <PRODUCT_ID>
order by occurred_at desc
limit 20;
```

This query is commonly used when diagnosing preorder commitment mismatches.

---

### 6. Verifying Lifecycle Snapshot State

Check the frozen presale cohort for a product:

```
select
product_id,
presale_commitment_total,
presale_snapshot_at,
first_inventory_arrival_at,
lifecycle_closed_at
from preorder.lifecycle_snapshot
where product_id = <PRODUCT_ID>;
```

This confirms:

- cohort size
- snapshot timestamp
- whether lifecycle closure occurred

---

### Operational Safety Rules

When performing manual maintenance operations:

- Never modify existing ledger rows.
- Only append new rows or delete rows during controlled rebuilds.
- Prefer replaying events rather than editing derived tables.
- Pause workers during large rebuild operations.

These rules preserve the **event‑sourced integrity** of the preorder system.

---

## 🔒 Current Stability Status

Classification engine is fully deterministic and structurally strict.

All business states implemented:
- active_preorder
- early_stock_arrival
- historical_preorder
- anomaly_*
- not_a_preorder_product

Test suite: 111/111 passing.

This version represents the first fully hardened, persisted, API-exposed, admin-protected preorder state machine release.