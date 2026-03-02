<file name=0 path=/Users/gilcalderon/shopify-projects/preorder-service/docs/test_matrix.md># Preorder-Service Classification Test Matrix (Rev 3 — FINAL FROZEN SPEC)

This document defines:
- all classification categories  
- exact conditions  
- expected outputs  
- test coverage requirements  
- priority order  
- field-by-field semantics  
- pytest cross-references  

This is the **final source of truth** for preorder-service classification.

---

## <details><summary>0. Definitions & Parsing Rules</summary>

### **Date Sources**

| Source | Format | Notes |
|--------|--------|-------|
| `date_tags[]` | MM-DD-YYYY | 0–many tags indicating pub-date history |
| `pub_date` metafield | YYYY-MM-DD | Current official pub date |
| `override_date` metafield | YYYY-MM-DD | Takes precedence over all |

### **Effective Pub Date Resolution (Rev 3)**

Choose ONE date using strict priority:

1. If `override_date` exists → **override_date**  
2. Else if `pub_date` exists → **pub_date** (`custom.pub_date`, authoritative source)  
3. Else if exactly one valid `date_tag` exists → **that single date_tag** (legacy fallback only)  
4. Else → **None**

Additional Rules:
- Date tags are legacy compatibility only and are not standard operating procedure.
- If multiple valid date tags exist:
  - If exactly one matches `pub_date`, resolution proceeds using `pub_date`.
  - Otherwise → classify as `anomaly_multi_date_conflict`.
- The system must not auto-select "latest" or "earliest" tag when multiple valid tags exist.

### **Inventory Semantics**

| Inventory | Meaning |
|----------|---------|
| `> 0` | stock present |
| `== 0` | standard preorder, not yet arrived |
| `< 0` | oversold preorder |

**Classification semantics:**
- preorder → `inventory <= 0`  
- early stock arrival → `inventory > 0`  
- historical → inventory ANY value  

</details>

---

## <details><summary>1. Priority Order (Rev 3 — Highest → Lowest)</summary>

1. **Anomalies** (`anomaly_*`)  
2. **Early Stock Arrival** (`early_stock_arrival`)  
3. **Active Preorder** (`active_preorder`)  
4. **Historical Preorder** (`historical_preorder`)  
5. **Not a Preorder Product** (`not_a_preorder_product`)  

</details>

---

## <details><summary>2. ANOMALIES (Highest Priority)</summary>

If ANY anomaly fires → return immediately.

---

### **2.1 anomaly_missing_tag**  
**Test File:** `tests/anomalies/test_anomaly_missing_tag.py`

**Condition**  
- `in_preorder_collection == True`  
- `'preorder'` NOT in tags  

**Expected**
```
status = "anomaly_missing_tag"
```

---

### **2.2 anomaly_missing_collection**  
**Test File:** `tests/anomalies/test_anomaly_missing_collection.py`

**Condition**  
- `'preorder'` in tags  
- NOT in preorder collection  
- At least one future signal  

**Expected**
```
status = "anomaly_missing_collection"
```

---

### **2.3 anomaly_pubdate_conflict (Revised)**  
**Test File:** `tests/anomalies/test_anomaly_pubdate_conflict.py`

Trigger Patterns:

- **Case A** – single date_tag present AND pub_date exists AND they differ  
- **Case B** – override_date exists AND differs from pub_date  
- **Case C** – single fallback tag present AND no pub_date exists BUT tag is malformed or invalid

**Expected**
```
status = "anomaly_pubdate_conflict"
```

---

### **2.4 anomaly_override_conflict**  
**Test File:** `tests/anomalies/test_anomaly_override_conflict.py`

Trigger Patterns:
- override_date < pub_date  
- override_date conflicts with authoritative pub_date  
- override_date is chronologically implausible relative to recorded historical effective_pub_date (future phase validation)

**Expected**
```
status = "anomaly_override_conflict"
```

---

### **2.5 anomaly_multi_date_conflict (Revised)**  
**Test File:** `tests/anomalies/test_anomaly_multi_date_conflict.py`

Condition:
- `len(valid_date_tags) >= 2`
- AND NOT (exactly one tag equals `pub_date`)

**Expected**
```
status = "anomaly_multi_date_conflict"
```

---

### **❌ Removed anomaly_inventory_contradiction**  
**Test File:** `tests/anomalies/test_anomaly_inventory_contradiction.py` (deprecated placeholder)

Use **early_stock_arrival** instead.

</details>

---

## <details><summary>3. EARLY STOCK ARRIVAL</summary>

**Test File:** `tests/test_early_stock_arrival.py`

**Condition**
- `effective_pub_date > today`  
- `inventory > 0`  
- AND product is structurally a preorder candidate:
  - (`'preorder'` in tags AND `in_preorder_collection == True`)
- AND no `anomaly_*` condition fires  

**Expected**
```
status = "early_stock_arrival"
```

**Precedence Rule**  
Early Stock Arrival is evaluated only AFTER all anomaly checks.  
If any anomaly condition is true, the anomaly must override early_stock_arrival.

</details>

---

## <details><summary>4. ACTIVE PREORDER</summary>

**Test File:** `tests/test_active_preorder.py`

**Condition**
- `effective_pub_date > today`
- `inventory <= 0`
- NOT `early_stock_arrival`
- NOT `anomaly_*`

**Expected**
```
status = "active_preorder"
```

</details>

---

## <details><summary>5. HISTORICAL PREORDER</summary>

**Test File:** `tests/test_historical_preorder.py`

**Condition**
- `'preorder'` in tags  
- all dates in past  
- NOT in preorder collection  
- no anomaly_*  
- not active  
- not early_stock_arrival  
- inventory ANY value  

**Expected**
```
status = "historical_preorder"
```

</details>

---

## <details><summary>6. NOT A PREORDER PRODUCT (Fallback)</summary>

**Test File:** `tests/test_not_a_preorder_product.py`

**Condition**
- no anomaly_*  
- not early_stock_arrival  
- not active  
- not historical  

**Expected**
```
status = "not_a_preorder_product"
```

</details>

---

## <details><summary>7. MASTER SUMMARY TABLE</summary>

### Structural Preorder Candidate Definition

A product is considered structurally preorder-eligible only when:

- `'preorder'` in tags  
  AND  
- `in_preorder_collection == True`

If tag and collection are misaligned, the product must classify as an anomaly.

Statuses `early_stock_arrival` and `active_preorder` require structural preorder eligibility.  
Otherwise the product falls through to `not_a_preorder_product` unless an anomaly fires.

| Priority | Condition | Result |
|----------|-----------|--------|
| 1 | any anomaly_* | anomaly_* |
| 2 | future effective pub date & inventory > 0 AND structurally preorder | early_stock_arrival |
| 3 | future effective pub date & inventory <= 0 AND structurally preorder | active_preorder |
| 4 | preorder tag + all dates past + not in collection | historical_preorder |
| 5 | all else | not_a_preorder_product |

</details>

---

## <details><summary>8. Test Coverage Requirements</summary>

### **Anomalies**
- missing_tag  
- missing_collection  
- pubdate_conflict  
- override_conflict  
- multi_date_conflict  

### **Statuses**
- early_stock_arrival  
- active_preorder  
- historical_preorder  
- not_a_preorder_product  

### **Cross-Category Negative Tests**
- future vs past dates  
- inventory >0 vs <=0  
- missing vs present metafields  
- tag + collection interactions  
- conflict prioritization  

### **Arrival Timing (Derived Layer)**
- no_arrival  
- early_arrival  
- on_time_arrival  
- late_arrival  
- pub_date required (NULL behavior)  
- 7-day boundary enforcement  
- ET-normalized date comparison  

</details>

---

## <details><summary>9. Date Resolution Test Matrix</summary>

| Available Signals | Effective Date | Reason |
|-------------------|----------------|--------|
| override only | override | highest priority |
| override + pub_date | override | override wins |
| pub_date only | pub_date | authoritative source |
| single date_tag only | date_tag | legacy fallback |
| multiple date_tags + pub_date match | pub_date | canonical alignment |
| multiple date_tags (no match) | anomaly_multi_date_conflict | ambiguous tags not allowed |
| no dates anywhere | None | cannot derive a pub date |

</details>

---

This file represents the **final, locked specification** for preorder-service classification.

---

## <details><summary>10. Test Coverage Snapshot (v1.0 — Arrival Layer Integrated)</summary>

**Total Tests: 96**

### Breakdown by Category

**Anomalies (5 files)**
- missing_tag
- missing_collection
- pubdate_conflict
- override_conflict
- multi_date_conflict

**Core Statuses (4 files)**
- early_stock_arrival
- active_preorder
- historical_preorder
- not_a_preorder_product

**Utility / Temporal Layers**
- effective_pub_date resolution
- pubdate_history (baseline insert, change detection, normalization, idempotency)

**Physical State Layer**
- inventory_arrival (first positive detection, idempotency, decoupling from classification)
- arrival_timing derivation (pub-date anchored, 7-day boundary, immutable first arrival)

**Infrastructure Layer**
- persistence (Supabase upsert logic)
- orchestrator (domain → engine → temporal → physical → persistence wiring)
- shopify_service (GraphQL → ProductMetadata shaping)
- override_service (DB override precedence + reclassification trigger)
- reclassification (single + batch deterministic re-entry)

---

### Coverage Guarantees

The test suite guarantees:

- All anomaly_* states are mutually exclusive and prioritized
- Structural preorder eligibility is strictly enforced (tag + collection alignment)
- Effective pub date resolution priority is deterministic
- Future vs past behavior is fully covered
- Inventory polarity (>0 vs <=0) is covered for all preorder states
- Early stock arrival requires structural eligibility
- Active preorder requires structural eligibility
- Historical preorder strictly requires preorder tag + past dates
- Persistence layer always performs deterministic upsert
- Orchestrator layer is deterministic and batch-safe
- Fallback behavior is explicitly tested
- Shopify GraphQL shaping layer fully covered (collections, variants, metafields)
- inventory_item_id → product_id resolution covered
- Override precedence enforced: DB override > metafield override > pub_date > date_tags
- Async service layer isolated from domain classification engine
- Pub date changes are historically recorded before persistence upsert
- Date normalization prevents false-positive change detection
- Baseline initialization captured for first classification
- Idempotent pubdate history tracking enforced
- First physical inventory arrival captured immutably
- Inventory arrival layer remains decoupled from classification logic
- Arrival timing derived strictly from immutable first_positive_inventory_at
- Late arrival defined strictly as arrival_date > effective_pub_date
- On-time arrival defined as arrival_date within 7 days prior to pub_date (inclusive)
- Early arrival defined as >7 days prior to pub_date
- Arrival timing returns NULL when effective_pub_date is NULL
- Arrival timing layer remains fully decoupled from structural classification engine

---

### Stability Marker

This snapshot corresponds to:

- README version: `v1.0-arrival-layer`
- All tests passing (88/88)
- Structural enforcement locked (tag + collection required for active / early states)
- Temporal layer implemented (pubdate_history)
- Physical arrival layer implemented (inventory_arrival)
- Persistence upsert implemented (preorder.product_status)
- Clean domain layer established (no Shopify dependencies)
- Deterministic orchestrator layer integrated
- Shopify GraphQL integration stabilized (product + inventory_item paths)
- Domain classification remains pure and persistence-free
- Service layer separation enforced (Shopify → Domain → Engine → Temporal → Physical → Persistence)

Future revisions must update this section.

</details></file>