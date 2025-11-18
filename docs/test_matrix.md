# Preorder-Service Classification Test Matrix (Rev 3 — FINAL FROZEN SPEC)

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
2. Else if `pub_date` exists → **pub_date**  
3. Else if `date_tags` exist → **latest date_tag**  
4. Else → **None**

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

- **Case A** – one tag, no override, pub_date != tag  
- **Case B** – multiple tags, no override, pub_date != latest_tag  
- **Case C** – pub_date/override disagree with latest_tag (unless override_conflict is more appropriate)

**Expected**
```
status = "anomaly_pubdate_conflict"
```

---

### **2.4 anomaly_override_conflict**  
**Test File:** `tests/anomalies/test_anomaly_override_conflict.py`

Trigger Patterns:
- override_date < pub_date  
- override_date < latest_tag  
- override older than any recorded official date  

**Expected**
```
status = "anomaly_override_conflict"
```

---

### **2.5 anomaly_multi_date_conflict (Revised)**  
**Test File:** `tests/anomalies/test_anomaly_multi_date_conflict.py`

**Condition**
- `len(date_tags) >= 2`  
- `pub_date is None`  
- `override_date is None`  

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
- no anomalies  

**Expected**
```
status = "early_stock_arrival"
```

</details>

---

## <details><summary>4. ACTIVE PREORDER</summary>

**Test File:** `tests/test_active_preorder.py`

**Condition**

A. At least one future-dated PREORDER signal:
- `effective_pub_date > today`
- OR future date_tag (when no metafields)
- OR in_preorder_collection == True
- OR preorder tag + future behavior

B. `inventory <= 0`  
C. NOT early_stock_arrival  
D. NOT anomaly_*  

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

| Priority | Condition | Result |
|----------|-----------|--------|
| 1 | any anomaly_* | anomaly_* |
| 2 | future effective pub date & inventory > 0 | early_stock_arrival |
| 3 | future signal & inventory <= 0 | active_preorder |
| 4 | preorder tag + dates past + not in collection | historical_preorder |
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

</details>

---

## <details><summary>9. Date Resolution Test Matrix</summary>

| Available Signals | Effective Date | Reason |
|-------------------|----------------|--------|
| override only | override | highest priority |
| override + pub_date | override | override wins |
| pub_date only | pub_date | next priority |
| no metafields + date_tags | latest date_tag | tags record revision history |
| no dates anywhere | None | cannot derive a pub date |

</details>

---

This file represents the **final, locked specification** for preorder-service classification.