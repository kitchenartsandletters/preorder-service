Below is the complete, canonical test_matrix.md, fully aligned with Rev 3 of the preorder classification logic.

Copy/paste this directly into your repository.
This is now the frozen, authoritative test matrix for both the implementation and the pytest suite.

⸻

test_matrix.md

Preorder-Service Classification Test Matrix (Rev 3 — FINAL FROZEN SPEC)

This document defines:
	•	all classification categories
	•	exact conditions
	•	expected outputs
	•	test coverage requirements
	•	priority order
	•	field-by-field semantics

This is the final source of truth against which the classification engine must be tested.

⸻

0. Definitions & Parsing Rules

Date Sources

Source	Format	Notes
date_tags[]	MM-DD-YYYY	May contain 0, 1, or many tags representing pub-date history
pub_date metafield	YYYY-MM-DD	Current official pub date unless superseded by override
override_date metafield	YYYY-MM-DD	Takes precedence over all other sources

Effective Pub Date Resolution (Rev 3)

Choose ONE date using strict priority:
	1.	If override_date exists → effective_pub_date = override_date
	2.	Else if pub_date exists → effective_pub_date = pub_date
	3.	Else if date_tags exist → effective_pub_date = latest date_tag
	4.	Else → effective_pub_date = None

Inventory Semantics

Inventory	Meaning
> 0	Stock is physically present
== 0	Standard preorder state — not yet arrived
< 0	Oversold preorder — still preorder

For classification:
	•	Preorder → inventory <= 0
	•	Early stock arrival → inventory > 0
	•	Historical → inventory may be any value

⸻

1. Priority Order (Rev 3 — Highest to Lowest)
	1.	Anomalies (anomaly_*)
	2.	Early Stock Arrival (early_stock_arrival)
	3.	Active Preorder (active_preorder)
	4.	Historical Preorder (historical_preorder)
	5.	Not a Preorder Product (not_a_preorder_product) ← fallback

⸻

2. ANOMALIES (Highest Priority)

If ANY anomaly fires → no other category may be returned.

⸻

2.1 anomaly_missing_tag

Condition (ALL must be true)
	•	in_preorder_collection == True
	•	'preorder' NOT in tags

Expected Output

status = "anomaly_missing_tag"
anomaly_type = "anomaly_missing_tag"

Test Scenarios

Scenario	Inputs
Missing tag while in collection	collection=True AND missing tag
Irrelevant: inventory	>0, 0, <0
Irrelevant: dates	any


⸻

2.2 anomaly_missing_collection

Condition (ALL must be true)
	•	'preorder' in tags
	•	in_preorder_collection == False
	•	At least one future signal from:
	•	effective_pub_date > today
	•	OR future pub_date, future override_date, or future date_tag

Expected Output

status = "anomaly_missing_collection"


⸻

2.3 anomaly_pubdate_conflict (COMPLETELY REVISED)

Trigger Patterns

Case A — Single Tag, No Override
	•	len(date_tags) == 1
	•	override_date is None
	•	pub_date != date_tag

Case B — Multiple Tags, No Override
	•	len(date_tags) >= 2
	•	override_date is None
	•	pub_date exists
	•	pub_date != latest_tag

Case C — General Metafield/Tag Mismatch
	•	Tags exist
	•	pub_date or override_date exist
	•	Neither matches the latest date_tag
	•	AND the mismatch is not better classified as anomaly_override_conflict

Expected Output

status = "anomaly_pubdate_conflict"


⸻

2.4 anomaly_override_conflict

Trigger Patterns
	•	override_date < pub_date
	•	OR override_date < latest_tag
	•	OR override is chronologically older than any known “official” date signal

Expected Output

status = "anomaly_override_conflict"


⸻

2.5 anomaly_multi_date_conflict (REDEFINED)

Condition (ALL must be true)
	•	len(date_tags) >= 2
	•	pub_date is None
	•	override_date is None

Interpretation:

Multiple historical pub dates exist but there is no canonical value (neither pub_date nor override_date is present to define the current one).

Expected Output

status = "anomaly_multi_date_conflict"


⸻

❌ Removed Anomaly from Rev 2

anomaly_inventory_contradiction is abolished.
Use early_stock_arrival instead.

⸻

3. EARLY STOCK ARRIVAL (Second Priority)

Condition (ALL must be true)
	•	effective_pub_date > today
	•	inventory > 0
	•	no anomaly_* triggered

Expected Output

status = "early_stock_arrival"

Notes

This is NOT an anomaly — but it must be surfaced.

⸻

4. ACTIVE PREORDER (Third Priority)

Condition (ALL must be true)

A. At least one future-dated PREORDER signal:
	•	effective_pub_date > today
OR
	•	A future date_tag (when no metafields)
OR
	•	in_preorder_collection == True
OR
	•	'preorder' in tags AND dates indicate future sale period

B. inventory <= 0
C. Not early_stock_arrival
**D. Not anomaly_*`
Expected Output

status = "active_preorder"


⸻

5. HISTORICAL PREORDER (Fourth Priority)

Condition (ALL must be true)
	•	'preorder' in tags
	•	All dates are past:
	•	If effective_pub_date exists → effective_pub_date <= today
	•	Else → max(date_tags) <= today
	•	in_preorder_collection == False
	•	no anomaly_*
	•	not early_stock_arrival
	•	not active_preorder
	•	inventory ANY value allowed (>0, 0, <0)

Expected Output

status = "historical_preorder"


⸻

6. NOT A PREORDER PRODUCT (Final Fallback)

Condition (ALL must be true)
	•	No anomaly_*
	•	Not early_stock_arrival
	•	Not active_preorder
	•	Not historical_preorder

Expected Output

status = "not_a_preorder_product"

This covers the majority of the store catalog.

⸻

7. MASTER SUMMARY TABLE

Priority	Condition	Result
1	Any anomaly rule matches	anomaly_*
2	Future effective_pub_date AND inventory > 0	early_stock_arrival
3	Future-signal AND inventory <= 0	active_preorder
4	Preorder tag + dates past + not in collection	historical_preorder
5	Everything else	not_a_preorder_product


⸻

8. Test Coverage Requirements by Category

Anomalies
	•	missing_tag
	•	missing_collection
	•	pubdate_conflict (rev’d rules)
	•	override_conflict
	•	multi_date_conflict (rev’d rules)

Status Types
	•	early_stock_arrival
	•	active_preorder
	•	historical_preorder
	•	not_a_preorder_product

Cross-category negatives

Each category must be tested against the others to prevent incorrect classification under:
	•	future vs past date evaluation
	•	inventory >0 vs <=0
	•	missing vs present metafields
	•	tag combinations
	•	collection membership interactions

⸻

9. Date Resolution Test Matrix

available signals	chosen effective date	reason
override only	override	highest priority
override & pub_date	override	override wins
pub_date only	pub_date	next priority
no metafields + date_tags	latest date_tag	tags represent revision history
no dates anywhere	None	cannot classify on date-based logic


⸻

This file represents the final, locked specification for preorder-service classification.