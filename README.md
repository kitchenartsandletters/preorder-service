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

## ⏳ Phase 9 — Weekly Release Engine
- Generate release-ready lists
- Exclude anomalies
- Include early stock arrivals
- Support approval gating
- Produce deterministic weekly snapshot outputs
- Prepare for Slack / reporting integration

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

Status: IN PROGRESS

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