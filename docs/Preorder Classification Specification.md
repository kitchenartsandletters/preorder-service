# Preorder Classification Specification  
**Canonical Logic Contract for preorder-service**

This document defines:
1. The authoritative inputs used for preorder classification  
2. The rules governing preorder identity  
3. The anomaly taxonomy  
4. The final output categories the classifier must emit  

This document is *binding* for both backend implementation and Admin Dashboard UI logic.

---

# 1. INPUTS  
The classifier receives product-level signals from Shopify:

## A. Preorder identity indicators  
1. **Collection membership**  
   - Product is in Shopify "Preorder" automated collection  
   - Boolean: `in_preorder_collection`

2. **Permanent historical tag**  
   - Product has the tag `'preorder'`  
   - Once added, NEVER removed  
   - Boolean: `has_preorder_tag`

3. **Date tags (MM-DD-YYYY)**  
   - May be one or more  
   - May reflect multiple publication dates (changed pub dates)  
   - Parsed as standard dates  
   - Array: `date_tags: list[date]`

4. **Primary pub date metafield**  
   - `custom.pub_date` (YYYY-MM-DD)  
   - Represents the "current official" publication date  
   - `pub_date: date | None`

5. **Override pub date metafield**  
   - `custom.preorder_override_date`  
   - Used when pub dates slip  
   - Override ALWAYS takes precedence over `pub_date`  
   - `override_date: date | None`

6. **Inventory**  
   - Integer, may be negative  
   - Critical for distinguishing early arrivals  
   - `inventory: int`

---

# 2. EFFECTIVE PUB DATE  
The classifier determines a single `effective_pub_date` using the following authoritative resolution order:

1. If `override_date` exists → use override  
2. Else if `pub_date` exists → use primary pub date (`custom.pub_date`, authoritative Shopify source)  
3. Else if exactly one valid date tag exists → use that single date tag (legacy fallback only)  
4. Else → `effective_pub_date = None`

Additional rules:

- Date tags are legacy compatibility only and are not standard operating procedure.
- If multiple valid date tags exist:
  - If exactly one of them matches `pub_date`, the classifier may proceed using `pub_date`.
  - Otherwise → classify as `anomaly_multi_date_conflict`.
- Malformed or unparsable date tags are ignored for effective_pub_date resolution but may trigger anomalies if they create ambiguity.

The preorder-service persists and owns `effective_pub_date` once resolved. Shopify reflects only the current product state; historical pub date changes must be recorded internally by the state machine.

---

## 2.1 PUB DATE CHANGE POLICY

Publication dates may legitimately change (e.g., publication slips). The system must honor authoritative updates while preserving lifecycle stability.

Rules:

- If `custom.pub_date` changes:
  - Update `effective_pub_date`.
  - Record the previous effective pub date internally (state machine responsibility).
  - Trigger full reclassification.
- If `override_date` changes:
  - It immediately supersedes all other pub date signals.
  - Reclassification must be triggered.
- Pub date history is not inferred from Shopify; it must be explicitly persisted by preorder-service.

This ensures that pub date updates are honored (NYT-compatible behavior) while preventing uncontrolled lifecycle drift caused by tag churn.

---

## 2.2 PUB DATE HISTORY (STATE MACHINE RESPONSIBILITY)

The classifier itself remains a pure function. It does not read historical state and does not compute transitions across time.

However, the preorder-service state machine must persist effective_pub_date transitions in the table:

    preorder.pubdate_history

This table records:

- product_id
- old_effective_pub_date
- new_effective_pub_date
- change_source
- engine_version
- changed_at

Rules:

1. A baseline row is inserted the first time a product is classified and effective_pub_date is resolved.
2. A new row is inserted whenever effective_pub_date changes.
3. Date comparison must be normalized (date vs ISO string safe comparison).
4. Pub date history insertion must occur BEFORE product_status upsert.
5. Pub date history tracking must be idempotent.

Change source values:

- initial_baseline
- shopify_pub_date
- override_date
- legacy_tag_fallback

This historical tracking layer is orthogonal to classification logic. It exists to preserve temporal integrity for:

- Reporting systems (e.g., NYT compatibility)
- Lifecycle state transitions
- Arrival timing classification (future phases)
- Auditability of publication slips

The classifier must never depend on pubdate_history for decision-making.

---

## 2.3 FUTURE EXTENSIONS (PLANNED)

Future phases of the preorder-service will introduce additional orthogonal dimensions that depend on `effective_pub_date` but are not part of structural preorder identity:

- Inventory arrival timing (e.g., first_positive_inventory_at)
- Commitment-aware lifecycle states (e.g., late_preorder, closed_preorder)

These dimensions are intentionally separated from structural preorder identity and anomaly detection logic.

---

## 2.4 INVENTORY ARRIVAL (STATE MACHINE RESPONSIBILITY)

The classifier remains a pure function and does not inspect historical inventory transitions.

However, the preorder-service state machine must persist the first time a product's inventory becomes positive in the table:

    preorder.inventory_arrival

This table records:

- product_id
- first_positive_inventory_at
- engine_version
- created_at

Rules:

1. A row is inserted when:
       inventory > 0
       AND no existing arrival row exists for that product.
2. The first_positive_inventory_at value is immutable once written.
3. Arrival tracking applies to ALL products (not preorder-restricted).
4. No transition detection is required (no <=0 → >0 logic).
5. Inventory arrival persistence must be idempotent.
6. Inventory arrival tracking must execute before product_status upsert.
7. The classifier must never depend on inventory_arrival for decision-making.

Inventory arrival tracking is orthogonal to:

- Structural preorder identity
- Anomaly detection
- Pub date resolution

It exists to preserve physical stock history for future phases, including:

- Early / on-time / late arrival timing derivation
- Commitment-aware lifecycle state transitions
- Operational auditing

The classifier must remain unaware of arrival history to preserve purity and determinism.

---

## 2.5 ARRIVAL TIMING DERIVATION (DERIVED LAYER)

Arrival timing is a derived dimension and is NOT part of structural preorder classification.

It is computed from:

- `effective_pub_date` (from product_status)
- `first_positive_inventory_at` (from inventory_arrival)

Arrival timing must NOT:
- Influence structural preorder identity
- Influence anomaly detection
- Influence effective_pub_date resolution
- Be persisted as a mutable field

Arrival timing categories:

- `no_arrival`
- `early_arrival`
- `on_time_arrival`
- `late_arrival`

Derivation rules:

1. If `effective_pub_date` is NULL → arrival_timing = NULL  
   (Pub date is required to anchor timing.)

2. If no inventory_arrival row exists → arrival_timing = `no_arrival`

3. Normalize `first_positive_inventory_at` to ET and convert to date:
       arrival_date_et

4. If arrival_date_et > effective_pub_date → `late_arrival`

5. Else compute:
       days_diff = (effective_pub_date - arrival_date_et).days

   - If days_diff <= 7 → `on_time_arrival`
   - Else → `early_arrival`

Definitions:

- Late arrival = inventory received AFTER the pub date.
- On-time arrival = inventory received on the pub date or within 7 days prior.
- Early arrival = inventory received more than 7 days before the pub date.
- Arrival timing is immutable once first_positive_inventory_at is written.
- Pub date changes may cause arrival timing to re-derive deterministically.
- Arrival timing remains fully decoupled from the classifier engine.

Arrival timing exists to support:

- Operational visibility
- Commitment-aware lifecycle states (future phases)
- Reporting alignment
- Audit integrity

The classifier must remain unaware of arrival timing to preserve purity and determinism.

# 3. PREORDER STATE CATEGORIES (FINAL, AUTHORITATIVE)

## 3.1 STRUCTURAL PREORDER IDENTITY

A product satisfies structural preorder identity if:
	•	has_preorder_tag == True
	AND
	•	in_preorder_collection == True

Both signals must be present and aligned.

Date signals alone (future pub_date, override_date, or date_tags) do NOT establish preorder identity.

If structural alignment is incomplete (tag without collection, or collection without tag), the product is classified as an anomaly.

If neither structural signal is present, the product is classified as not_a_preorder_product, unless a defined anomaly condition explicitly applies (e.g., inventory contradiction).
Structural identity is evaluated after anomaly detection.

---

## 3.2 EARLY STOCK ARRIVAL

A product is classified as early_stock_arrival when:
	•	It satisfies structural preorder identity
	•	effective_pub_date > today
	•	inventory > 0
	•	No anomalies are present

Definition:
   early_stock_arrival =
      structurally_preorder
      AND effective_pub_date > today
      AND inventory > 0
      AND no_anomalies

This represents books that have physically arrived before their official publication date.

## 3.3 ACTIVE PREORDER

A product is classified as active_preorder when:
	•	It satisfies structural preorder identity
	•	effective_pub_date exists AND effective_pub_date > today
	•	inventory <= 0
	•	No anomalies are present

Definition:

   active_preorder =
      structurally_preorder
      AND effective_pub_date > today
      AND inventory <= 0
      AND no_anomalies

Early stock arrival is evaluated before active_preorder.

## 3.4 HISTORICAL PREORDER

A product is classified as historical_preorder when:
	•	has_preorder_tag == True
	•	effective_pub_date is None OR effective_pub_date <= today
	•	in_preorder_collection == False
	•	No anomalies are present

Inventory level does not affect historical classification.

Definition:

   historical_preorder =
      has_preorder_tag
      AND (effective_pub_date is None OR effective_pub_date <= today)
      AND not in_preorder_collection
      AND no_anomalies

Historical classification is only valid when structural preorder identity is no longer satisfied.


---

# 4. ANOMALY CATEGORIES (MUST BE FULLY IMPLEMENTED)

All anomaly states override active_preorder, early_stock_arrival, and historical_preorder classifications.
Anomalies are evaluated before structural identity.

## 4.1 `anomaly_missing_tag`
- Product **in** Preorder Collection  
- BUT missing `'preorder'` tag  

## 4.2 `anomaly_missing_collection`
- Product **tagged** `'preorder'`  
- BUT NOT in Preorder collection  
- AND has a future pub/on-sale/override date  

## 4.3 `anomaly_pubdate_conflict`
- Conflicting pub dates between:  
  - date tag(s)  
  - primary pub field  
  - override pub field  
- Example: date tag says “10-07-2025” but metafield says “2025-09-15”

## 4.4 `anomaly_override_conflict`
- Override date exists  
- But is *earlier* than the real pub date  
- Or contradicts Shopify’s automated date tag ordering

## 4.5 `anomaly_multi_date_conflict`
- Two or more date tags where the later date is *earlier* than the earlier tag  
- Example: “07-01-2025” AND “03-01-2025”

## 4.6 `anomaly_inventory_contradiction`
- Future pub date
- Inventory > 0
- BUT product does not satisfy structural preorder identity
	•	(e.g., missing 'preorder' tag AND not in preorder collection)

This anomaly applies only when effective_pub_date > today.

---

# 5. FINAL CLASSIFICATION OUTPUT

The classifier must return:
   {
   status: 
      "active_preorder" |
      "early_stock_arrival" |
      "historical_preorder" |
      "anomaly_*" |
      "not_a_preorder_product",

   anomaly_type: str | None,
   effective_pub_date: date | None
   }

---

# 6. NON-GOALS (OUT OF SCOPE FOR CLASSIFIER)
- No decisions about NYT reporting  
- No Slack notifications  
- No admin approvals  
- No automated publish/unpublish  
- No GitHub issues  
- No Shopify writes  

These are downstream concerns.

                     ┌──────────────────────────┐
                     │ START: classify(product) │
                     └───────────────┬──────────┘
                                     │
                                     ▼
                     ┌────────────────────────────────┐
                     │ 1. CHECK FOR ANOMALIES FIRST   │
                     └────────────────────────────────┘
                        │
                        ├── anomaly_missing_tag ?
                        ├── anomaly_missing_collection ?
                        ├── anomaly_pubdate_conflict ?
                        ├── anomaly_override_conflict ?
                        ├── anomaly_multi_date_conflict ?
                        └── anomaly_inventory_contradiction ?
                                │
                                ├── YES → status = anomaly_*
                                └── NO
                                     │
                                     ▼
                     ┌────────────────────────────────────┐
                     │ STRUCTURAL PREORDER IDENTITY    │
                     └────────────────────────────────────┘
                           Condition:
                             - has_preorder_tag
                               AND
                             - in_preorder_collection
                           │
                           ├── NO → not_a_preorder_product
                           └── YES → continue
                                │
                                ▼
                     ┌────────────────────────────────────┐
                     │ 2. EARLY STOCK ARRIVAL?            │
                     └────────────────────────────────────┘
                           Condition:
                             - structurally_preorder
                             - effective_pub_date > today
                             - AND inventory > 0
                           │
                           ├── YES → status = early_stock_arrival
                           └── NO
                                │
                                ▼
                     ┌────────────────────────────────────────┐
                     │ 3. ACTIVE PREORDER?                    │
                     └────────────────────────────────────────┘
                           Conditions:
                             - structurally_preorder
                             - effective_pub_date exists
                             - effective_pub_date > today
                             - inventory <= 0
                           │
                           ├── YES → status = active_preorder
                           └── NO
                                │
                                ▼
                     ┌────────────────────────────────────────┐
                     │ 4. HISTORICAL PREORDER?               │
                     └────────────────────────────────────────┘
                           Conditions:
                             - 'preorder' in tags
                             - all dates <= today
                             - NOT in preorder collection
                           │
                           ├── YES → status = historical_preorder
                           └── NO
                                │
                                ▼
                     ┌────────────────────────────┐
                     │ 5. NOT A PREORDER PRODUCT  │
                     └────────────────────────────┘