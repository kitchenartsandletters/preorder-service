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

## Phase 10 — Pub Date History Tracking
- Persist `effective_pub_date` changes internally
- Record historical transitions (previous value, changed_at, source)
- Trigger deterministic reclassification on pub date updates

## Phase 11 — Inventory Arrival Timing
- Track `first_positive_inventory_at`
- Classify arrival timing:
  - no_arrival
  - early_arrival
  - on_time_arrival
  - late_arrival
- Arrival timing is immutable once first positive delta occurs

## Phase 12 — Commitment-Aware Lifecycle States
- Introduce commitment ledger integration
- Add lifecycle states:
  - late_preorder
  - closed_preorder
  - open_historical_preorder
- Separate lifecycle state from structural preorder identity

These phases expand the state machine without altering the frozen canonical rules above.

---

Classification engine is stable through Phase 8 (API-integrated, persisted, and admin-protected).

---

## 🧪 Test Coverage Snapshot — v0.8-shopify-integration

Total test count: **83 tests — 100% passing**

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

Version Tag: `v0.8-shopify-integration`

---
## 🔒 Current Stability Status

Classification engine is fully deterministic and structurally strict.

All business states implemented:
- active_preorder
- early_stock_arrival
- historical_preorder
- anomaly_*
- not_a_preorder_product

Test suite: 83/83 passing.

This version represents the first fully hardened, persisted, API-exposed, admin-protected preorder state machine release.