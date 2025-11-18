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
The classifier determines a single `effective_pub_date` using priority:

1. If `override_date` exists → use override  
2. Else if `pub_date` exists → use primary pub date  
3. Else if date_tags exist → use the earliest valid date tag  
4. Else → `effective_pub_date = None`

---

# 3. PREORDER STATE CATEGORIES (FINAL, AUTHORITATIVE)

## 3.1 ACTIVE PREORDER  
A product is an active preorder **if ANY of these are true**:
- It is in the Preorder collection  
- It has a preorder tag  
- It has a date tag in the future  
- It has `pub_date` or `override_date` in the future  

AND it has **no anomalies** (below).

Definition:
active_preorder = one_future_date_signal AND no_anomalies

---

# 3.2 HISTORICAL PREORDER  
A product was previously a preorder but is no longer:
- Has permanent `'preorder'` tag  
- All dates (override/pub/date_tag) are in the past  
- Inventory is normal  
- Not in the Preorder collection

Definition:  
historical_preorder = preorder_tag AND all_dates_past AND not_in_preorder_collection

---

# 4. ANOMALY CATEGORIES (MUST BE FULLY IMPLEMENTED)

Anomalies override active/historical classification.

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
- BUT Shopify shows positive inventory  
- Not accounted for as early arrival exception  

---

# 5. FINAL CLASSIFICATION OUTPUT

The classifier must return:
{
status: “active_preorder” | “historical_preorder” | “anomaly_*”,
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
                        └── anomaly_multi_date_conflict ?
                                │
                                ├── YES → status = anomaly_*
                                └── NO
                                     │
                                     ▼
                     ┌────────────────────────────────────┐
                     │ 2. EARLY STOCK ARRIVAL?            │
                     └────────────────────────────────────┘
                           Condition:
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
                             - any future-dated signal:
                                * effective_pub_date > today
                                * future date_tag
                                * in_preorder_collection
                                * preorder tag + future behavior
                             - AND inventory <= 0
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