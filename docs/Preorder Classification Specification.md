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

## 2.2 FUTURE EXTENSIONS (PLANNED)

Future phases of the preorder-service will introduce additional orthogonal dimensions that depend on `effective_pub_date` but are not part of structural preorder identity:

- Inventory arrival timing (e.g., first_positive_inventory_at)
- Commitment-aware lifecycle states (e.g., late_preorder, closed_preorder)
- Historical pub date tracking and transition audit

These dimensions are intentionally separated from structural preorder identity and anomaly detection logic.

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