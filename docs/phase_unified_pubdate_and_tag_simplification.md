# Phase Proposal — Unified Pub-Date System, Tag Simplification, Override Collapse

**Status: DRAFT PROPOSAL.** Not yet an authoritative spec. Approved in principle
(decisions locked below); implementation to be executed by a dedicated chat.
Check `docs/DOCS_STATUS.md` before trusting any other doc referenced here.

Author context: drafted at the end of a long preorder-service engagement, grounded
in a direct read of the current code (files and line-level behavior cited inline so
the executing chat does not have to re-derive them — but re-verify anything before
you depend on it; code moves).

Last updated: 2026-09-19

---

## 0. One-paragraph summary

Collapse the product date model from **three date signals** (`override_date`
metafield → `pub_date` metafield → `date_tags`) to **one authoritative
`pub_date`** with a structured **`pub_date_history`** audit table. Retire the
`override_date` metafield entirely (a "backdate" becomes an ordinary pub_date
change recorded in history). Deprecate and delete the `MM-DD-YYYY` `date_tags`
after mining them into history. Keep the `preorder` tag but **demote** it: the
classifier stops requiring it and derives historical status from the database.
Make the **admin-dashboard the sanctioned writer** of pub dates (writes the
metafield + a history row + triggers reclassification synchronously), with direct
Shopify metafield edits still intaken as a graceful-degradation backstop. Two
titles currently blocked by the override-conflict anomaly are the Move 1 test
cases.

---

## 1. Locked decisions (do not relitigate without the owner)

1. **Override collapse — DECISION A.** Retire the `override_date` metafield. There
   is exactly one authoritative date: `pub_date`. Backdating = a pub_date change
   through the UI, recorded in history. The `anomaly_override_conflict` class is
   **eliminated**, not managed with an approval flow. (Rejected alternative B:
   keep `override_date` as a second field with an approval mechanism. Only correct
   if override ever meant "a second simultaneously-true date"; it does not — from
   the blocked titles it is always "the date moved earlier.")
2. **date_tags — deprecate and DELETE entirely**, but **mine them into
   `pub_date_history` first** (backfill depth = full history reconstruction, not
   current-date-only). After history is captured and surfaced in the UI, strip the
   `MM-DD-YYYY` tags from products and stop the classifier reading them.
3. **`preorder` tag — KEEP, DEMOTE.** Stays as a cheap human-readable marker.
   Stops being *required* for `historical_preorder`. Classifier derives historical
   status from DB history instead.
4. **pub_date metafield = source of truth.** The admin-dashboard is the sanctioned
   writer. SOP: users do not edit the metafield directly in Shopify. But the SOP
   is a social control, not technical — the system must degrade gracefully when it
   is violated (see §6, backstop).
5. **Test cases for Move 1:** `Saint Peter: Chapter & Verse` (product_id
   `7300376330373`) and `Bordeaux Chronicle` (product_id `7265304543365`). Both are
   currently `anomaly_override_conflict` because override_date is earlier than
   pub_date. Move 1 must resolve both to their correct live status
   (`active_preorder` — both future-dated and in the Preorder collection).

---

## 2. Verified current state (as of this writing — re-verify before building)

### 2.1 Date resolution — `classification/utils.py::resolve_effective_pub_date`
Priority order (Rev 3, FINAL in current code):
1. `override_date` (metafield)
2. `pub_date` (metafield)
3. latest `date_tag` — `max(date_tags)`; tags are `MM-DD-YYYY`, unsorted, treated
   as a revision history
4. `None`

### 2.2 Override conflict — `classification/engine.py::_detect_anomaly`
`anomaly_override_conflict` fires in three cases:
- **Case 1:** `override_date < pub_date` (this is what blocks the two test titles)
- **Case 2:** `override_date < max(date_tags)`
- **Case 3:** `override_date < today AND pub_date > today`

Collapsing to one date (Decision A) removes the *reason* all three exist for
date-vs-date disagreement. Cases 1 and 2 become impossible (no second date to
disagree with). Case 3 is subsumed by normal past/future handling of the single
`pub_date`.

### 2.3 Other date-tag-dependent anomalies (also retired by this phase)
- `anomaly_pubdate_conflict` — `pub_date != max(date_tags)`.
- `anomaly_multi_date_conflict` — `>= 2 date_tags AND pub_date is None AND
  override_date is None`.
Both vanish once date_tags stop being a resolution source.

### 2.4 The `preorder` tag's current load-bearing use
- `classification/engine.py`: `historical_preorder` requires `_has_preorder_tag`
  (`"preorder" in tags`) AND `not in_preorder_collection` AND past/None effective
  date. **Remove the tag from a historical title today and it misclassifies to
  `not_a_preorder_product`.** This is the coupling Move 3 removes.
- Collection membership (`in_preorder_collection`) is the *authoritative* live-
  preorder signal and is NOT changing.

### 2.5 Intake path — `routes/webhooks.py`
- `products/update` is handled **synchronously**: `_process_product_update` →
  `build_product_metadata_from_shopify` → `reclassify_single_product` inline in the
  request. So once a webhook arrives, reclassification is immediate.
- **RISK / OPEN:** whether a *metafield-only* edit reliably emits `products/update`
  depends on the webhook subscription config on the **webhook-gateway** side (not
  in this repo). Shopify metafield edits do not always fire `products/update`.
  This fragility is a core argument for the UI-owns-writes design: the UI calls
  `reclassify_single_product` directly and does not depend on webhook delivery.
- `reclassify_single_product` (in `services/reclassification_service.py`) is the
  shared, safe reclassify entry point — reuse it from the UI write path; do not
  reinvent.

### 2.6 Where metadata flows through (touch-points to update)
- `shopify_service.py::build_product_metadata_from_shopify` — builds
  `ProductMetadata` from Shopify (reads tags, override_date_raw, collections).
  `_extract_date_tags_raw` uses `DATE_TAG_RE`.
- `domain_models.py::ProductMetadata` — carries `tags`, `pub_date_raw`,
  `override_date_raw`, `parsed_date_tags()`.
- `classification/types.py::ClassificationInput` — `tags`, `date_tags`,
  `pub_date`, `override_date`, `in_preorder_collection`.
- `orchestrator.py` and `audits/shopify_alignment_audit.py` — both build
  `ClassificationInput` the same way; both must be updated when the input shape
  changes.
- `persistence.py` — writes `metadata_snapshot` including `tags`, `pub_date`,
  `override_date`, `in_preorder_collection` into `product_status`.
- Bulk importer (`preorder-bulk-importer/`) — sets date tags + preorder capsule
  tags at creation; `docs/canonical-map.md` documents the CSV mapping. This is the
  primary source of the date_tag glut and must change so new products stop
  accumulating tags.

---

## 3. Target model

### 3.1 Single authoritative date
- `pub_date` (Shopify metafield) is the only date signal the classifier reads.
- `override_date` metafield is **retired** (removed from products; removed from
  `ProductMetadata`, `ClassificationInput`, `resolve_effective_pub_date`, and the
  override-conflict anomaly logic).
- `date_tags` are **removed** from resolution and from products (after backfill).

### 3.2 `pub_date_history` (new table, `preorder` schema)
```
preorder.pub_date_history (
    id            bigserial primary key,
    product_id    bigint not null,
    pub_date      date not null,          -- the date as of this change
    previous_date date,                    -- the date it replaced (null for first)
    source        text not null,           -- see enum below
    changed_by    text,                    -- UI user identity if available, else null
    reason        text,                    -- optional operator note
    changed_at    timestamptz not null default now()
)
-- index on (product_id, changed_at desc)
```
`source` values:
- `ui_change` — sanctioned dashboard edit
- `ui_backdate` — sanctioned dashboard edit where new date < previous date
  (replaces the old override concept)
- `shopify_direct_edit` — detected via webhook; a metafield edit that bypassed the
  UI (SOP violation, recorded not rejected)
- `migration_backfill` — seeded from current effective date at migration
- `tag_history_backfill` — reconstructed from a mined `date_tag`
- `bulk_import` — set at product creation by the importer

### 3.3 `preorder` tag — advisory only
- Classifier derives "was/is a preorder" from the database: a `product_status` row
  whose lifecycle includes `active_preorder`/`early_stock_arrival`, or the
  existence of a `release_state` row, establishes historical-preorder eligibility.
- `historical_preorder` no longer requires `_has_preorder_tag`. Tag presence
  becomes advisory metadata (kept in `metadata_snapshot`, shown in UI, not gating).
- Net effect is *stronger*: removing/omitting the tag can no longer misclassify a
  historical title.

---

## 4. The three moves and their hard ordering

Ordering is a dependency chain, not a preference. **Do not reorder.**

### Move 1 — Unified pub-date + history + override collapse (FOUNDATION)
Resolves the two blocked titles and the entire override-conflict class.
Deliverables:
1. `pub_date_history` table (§3.2) + migration.
2. Backfill history with one `migration_backfill` row per product (current
   `effective_pub_date`).
3. UI write-path (admin-dashboard): a pub-date editor that (a) writes the Shopify
   `pub_date` metafield, (b) inserts a `pub_date_history` row (`ui_change` or
   `ui_backdate`), (c) calls `reclassify_single_product` synchronously, (d)
   refreshes the row. This replaces direct metafield editing.
4. Classifier: retire `override_date` from `resolve_effective_pub_date`,
   `ClassificationInput`, `ProductMetadata`, and delete override-conflict Cases
   1–3 from `_detect_anomaly`. Update `orchestrator.py` and
   `audits/shopify_alignment_audit.py` input construction.
5. Retire the `override_date` metafield on products (a one-time Shopify cleanup;
   value, if meaningful and earlier, is folded into `pub_date` + a `ui_backdate`
   history row during migration — see §5).
6. Tests: extend the classifier suite (mirror the delayed-import change pattern) —
   assert override-conflict cases no longer fire; assert the two test titles
   classify as `active_preorder`.
7. UI history panel (read-only) surfacing `pub_date_history` per product — needed
   before Move 2 deletes the tags.

**Move 1 acceptance:** Saint Peter (`7300376330373`) and Bordeaux Chronicle
(`7265304543365`) both classify `active_preorder`, no anomaly; each has a
`pub_date_history` showing the corrected (earlier) date with the prior date in
`previous_date`; the UI history panel shows it.

### Move 3 — DB-derived historical status (do BEFORE Move 2)
Deliverables:
1. Classifier: `historical_preorder` eligibility derives from DB history
   (product_status lifecycle / release_state), not `_has_preorder_tag`.
2. Provide the DB signal to the classifier (either as a new `ClassificationInput`
   field populated by the reclassify service, or by having the service pass a
   `was_preorder` boolean it computes from Supabase).
3. Tests: a historical title with the tag REMOVED still classifies
   `historical_preorder`; a never-preorder product still classifies
   `not_a_preorder_product`.

Why before Move 2: deleting date_tags and demoting the tag both reduce reliance on
Shopify-side signals; historical status must already be DB-derived so nothing
regresses when tags thin out.

### Move 2 — Delete date_tags (CLEANUP, do LAST)
Deliverables:
1. Backfill: mine every product's existing `MM-DD-YYYY` `date_tags` into
   `pub_date_history` as `tag_history_backfill` rows (ordered oldest→newest,
   `previous_date` chained). This is the "full history reconstruction" decision.
2. Verify the UI history panel renders the mined history.
3. Strip `MM-DD-YYYY` tags from products (Shopify write). Keep the `preorder`
   tag and any non-date capsule tags.
4. Classifier: remove `date_tags` from `resolve_effective_pub_date` and delete
   `anomaly_pubdate_conflict` and `anomaly_multi_date_conflict`.
5. Bulk importer: stop emitting `MM-DD-YYYY` date tags on new products; set
   `pub_date` metafield + a `bulk_import` history row instead. Update
   `preorder-bulk-importer/docs/canonical-map.md`.
6. Tests: date-tag-dependent anomaly tests removed; resolution tests updated to
   single-date.

**Move 2 acceptance:** no product carries `MM-DD-YYYY` tags; all prior tag-dates
are visible in the UI history; classifier reads only `pub_date`; importer creates
tag-light products.

---

## 5. Migration plan (Move 1 detail)

For each product at migration time:
1. Compute current `effective_pub_date` using the OLD logic (override → pub_date →
   max date_tag).
2. If an `override_date` exists AND differs from `pub_date`: set the authoritative
   `pub_date` metafield to the override value (that was the effective date), and
   write a `pub_date_history` row `source=ui_backdate` (or `migration_backfill` if
   you prefer not to imply UI origin — pick one convention and be consistent),
   with `previous_date` = the old `pub_date`.
3. Else: write a single `migration_backfill` row with the current effective date.
4. Do NOT delete date_tags in Move 1 (that is Move 2, after mining).
5. Retire the `override_date` metafield only after step 2 has captured its value.

Idempotency: migration must be safe to re-run (upsert history keyed so a second
run does not duplicate the seed row).

---

## 6. Graceful degradation — direct metafield edits (the SOP backstop)

The SOP says "edit pub dates only in the dashboard." Reality: someone will edit the
Shopify metafield directly. The system must not silently lose or mis-handle it.

- Keep the `products/update` webhook path (§2.5) active. When a metafield edit
  arrives out-of-band, reclassify as today AND append a `pub_date_history` row
  `source=shopify_direct_edit` if the new `pub_date` differs from the latest
  history row's date. This preserves the audit trail and flags the bypass.
- Do NOT reject the change — record it. Optionally surface a soft dashboard notice
  ("this date was changed outside the dashboard") so the audit shows the bypass.
- This is the reason the UI path and the webhook path both call the SAME
  `reclassify_single_product` — they must produce identical classification.

---

## 7. Explicit non-goals / guardrails

- **Collection membership stays authoritative** for live-preorder identity.
  Untouched.
- **The classifier stays a pure, tested function.** Every change updates the test
  suite; `docs/test_matrix.md` must be updated (currently marked "not re-verified"
  in DOCS_STATUS — audit it as part of this work).
- **No history is destroyed.** Move 1 and Move 2 both *add* structured history
  before removing the tag glut. Deletion follows capture, never precedes it.
- **`preorder` tag is not deleted**, only demoted.
- **Do not weaken to streamline:** each removed signal is replaced by a
  stronger DB-derived one (single SoT + history; DB-derived historical status),
  not by nothing.

## 8. Open items for the executing chat to confirm first

1. Verify §2 against live code before building (files move; this doc is a snapshot).
2. Confirm with the webhook-gateway owner whether metafield edits currently emit
   `products/update` — determines how much the §6 backstop is exercised in
   practice.
3. Decide the exact DB signal for Move 3 (`ClassificationInput` field vs. service-
   computed boolean) and update `orchestrator.py` + the audit script together.
4. Choose the migration `source` convention for backdated overrides (§5 step 2)
   and apply it consistently.
5. Update `docs/DOCS_STATUS.md` rows for the classification spec and test_matrix
   as they are touched.
