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



---

# Classification Engine Progress Snapshot

This section tracks the implementation state of preorder-service.

## ✅ Phase 1 — Effective Publication Date
Implemented:
- `resolve_effective_pub_date()` utility
- Priority order:
  1. override_date
  2. pub_date
  3. latest date_tag
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
        ('preorder' in tags OR in_preorder_collection == True)
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

## ⏳ Phase 8 — Admin Dashboard Integration
- Display product classification
- Surface anomalies
- Support override review
- Support manual reclassification

---

## ⏳ Phase 9 — Weekly Release Engine
- Generate release-ready lists
- Exclude anomalies
- Include early stock arrivals
- Support approval gating

---

Classification engine is currently stable through Phase 4 with full anomaly + early stock + active coverage.

---

## 🧪 Test Coverage Snapshot — v0.6-structural-strict

Total test count: **66 tests — 100% passing**

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

Version Tag: `v0.7-domain-orchestrator`

---
## 🔒 Current Stability Status

Classification engine is fully deterministic and structurally strict.

All business states implemented:
- active_preorder
- early_stock_arrival
- historical_preorder
- anomaly_*
- not_a_preorder_product

Test suite: 60/60 passing.

This version represents the first fully hardened, persisted, domain-isolated state machine release.