# Phase Roadmap (Rev 2) — Unified Pub-Date, Override Collapse, Tag Simplification

**Status: CURRENT — the authoritative plan for this phase. Not yet implemented.**
Supersedes `docs/phase_unified_pubdate_and_tag_simplification.md` (kept for the
trail; its premises carry errata E1–E7, recorded in `docs/DOCS_STATUS.md`). Do not
build from the original.

§2 describes the system as it is today. Everything else describes FUTURE state.

Grounding: `main` at `6677400`; production schema `preorder` in the
**webhook-gateway** Supabase project (`evzradwmnzcuwzckgtmv`); the
`admin-dashboard` repo at `ac7609a`; Shopify developer docs as of 2026-09-19.
Every count in this doc is a snapshot from 2026-09-19. Scripts must re-query live
state at run time and never hard-code these numbers.

Last updated: 2026-09-19

---

## 0. Summary

Collapse the date model to one source of truth: the Shopify `custom.pub_date`
metafield. The **existing** `preorder.pubdate_history` table becomes the audit trail;
it is extended, not replaced. Retire `override_date` from both of its sources: the
`custom.preorder_override_date` metafield and the `preorder.product_overrides`
table.

Replace the `preorder` tag as a classification input with a sticky, DB-owned
`preorder_since` column. Keep the tag as a human marker, and demote its absence
in-collection to a non-blocking hygiene flag.

Archive every date-shaped product tag before stripping them. The date-tag code is
already inert (E2), so removing it changes no classification.

Execution order is **Move 0 → Move 1 → Move 3 → Move 2**. Four blocking gates
(G1–G4, §5) sit at fixed points in that sequence.

---

## 1. Locked decisions

These were ruled by the owner on 2026-09-19. Do not relitigate them.

- **D1 — Override collapse (Decision A).** There is one authoritative date:
  `pub_date`. `override_date` is retired from both sources. The
  `anomaly_override_conflict` class is eliminated.
- **D2 — Extend `preorder.pubdate_history`.** There is no second history table. New
  `change_source` values are added (§3.1). `changed_by` and `reason` live in the
  `metadata` jsonb. The existing trail (since 2026-03-03) is preserved.
- **D3 — Migration label `override_migration`.** Direction is recorded in
  `metadata.direction`. The original proposal's `ui_backdate` label is **retired**:
  it rested on the false premise that an override always means an earlier date
  (E4).
- **D4 — Archive plus selective backfill for tags.**
  - Archive every date-shaped raw tag, as-is, to a dedicated table before any strip.
    This covers both formats and includes malformed tags, which are not repaired.
  - Backfill into history only the preorder-family dates not already there (5 as of
    2026-09-19).
  - The ~6,500 backlist dates stay in the archive and are not migrated.
  - The archive is non-negotiable insurance; the backfill is minimal.
- **D5 — Sticky `preorder_since` column is the Move 3 signal.** It is seeded once
  from the tag, set on preorder entry, and never cleared. It is the only signal
  available with full coverage and zero false positives (§2.6).
- **D6 — `anomaly_missing_tag` is demoted to a non-blocking hygiene flag.**
  - After Move 3, a title in the Preorder collection without the tag classifies to
    its real status via `preorder_since`.
  - "Missing expected preorder tag" surfaces separately as a boolean alongside that
    status, modeled exactly on the stock-received signal (§3.4).
- **D7 — No interim metafield edit for Saint Peter.**
  - The storefront already stamps `09-22` onto order lines.
  - The title self-resolves to `active_preorder` via the delayed-import path.
  - The anomaly is dashboard-cosmetic until Move 1 lands.
  - No out-of-band edits are introduced ahead of the system built to remove them.
- **D8 — The `preorder` tag is kept but demoted.** It stops gating classification. It
  is not deleted.
- **D9 — The dashboard is the sanctioned writer of `pub_date`.** Direct Shopify edits
  are recorded, not rejected (§3.6).
- **D10 — Test cases.** The test titles are `Saint Peter: Chapter & Verse`
  (`7300376330373`) and `Bordeaux Chronicle` (`7265304543365`). Acceptance is
  restated in §4 (Move 1).

---

## 2. Verified current state (2026-09-19)

### 2.1 Date resolution and anomalies

- `classification/utils.py::resolve_effective_pub_date` resolves in this order:
  override → `pub_date` → `max(date_tags)` → None.
- `classification/engine.py::_detect_anomaly` reads `override_date` in
  `anomaly_override_conflict` Cases 1–3 **and** in `anomaly_stale_collection`, which
  requires `override_date is None`.
- `anomaly_stale_collection` also reads `date_tags`, through the clause
  `pub_date == max(date_tags)`.

### 2.2 Date tags are inert on the live path (E2)

- `shopify_service._extract_date_tags_raw` keeps only tags that match `YYYY-MM-DD`.
- `ProductMetadata.parsed_date_tags()` then keeps only tags that match `MM-DD-YYYY`.
- No tag passes both filters, so `date_tags == []` for every product classified from
  Shopify.
- As a result, none of these can ever fire in production:
  - `anomaly_pubdate_conflict`
  - `anomaly_multi_date_conflict`
  - override Case 2
  - the date-tag clause of `stale_collection`
- The history table confirms it: there are 0 `legacy_tag_fallback` rows.
- **Do not "fix" the regex before Move 2.** Doing so would activate
  `pubdate_conflict` across thousands of products.
- Tag inventory, taken from `product_status.metadata_snapshot`:
  - 9,171 valid tags in `MM-DD-YYYY` form
  - 9 valid tags in `YYYY-MM-DD` form
  - 2 malformed tags: `02-29-2022` (not a real date) and `13-02-2020` (day-first)
- These tags sit on about 9,100 products.

### 2.3 Override has two sources (E3)

- **Metafield.** `custom.preorder_override_date`, read by `shopify_service`.
  - 36 products carry it.
- **DB table.** `preorder.product_overrides`, read by
  `override_service.fetch_override_date` in both the orchestrator and the alignment
  audit.
  - The DB value **wins** over the metafield.
  - The table holds one row, for product `12345`. That is test junk.
- **Dead write path.** `update_override_date_and_reclassify` is called by no route.
  - It would also fail if called, because it writes an `updated_by` column that the
    table lacks.
- **View dependency.** `vw_preorder_products.override_status` joins
  `product_overrides`.
  - `vw_preorder_release_queue` selects that column.
  - The dashboard renders it only in `PreorderDetailSidebar.tsx`.
  - The dashboard's API mapper defaults a missing value to `"none"`.

**The 36 overrides, by direction relative to `pub_date` (E4):**

| Direction | Count |
|---|---|
| Earlier (the two test titles) | 2 |
| Later — `historical_preorder` | 22 |
| Later — `active_preorder` | 6 |
| Later — `early_stock_arrival` | 1 |
| No `pub_date` at all | 4 |
| Equal to `pub_date` | 1 |

In every case the effective date already equals the override value. **Folding
override into `pub_date` changes zero effective dates.**

### 2.4 Existing history — `preorder.pubdate_history` (E1)

- **Written by:** `orchestrator.classify_and_persist_product` whenever the effective
  date changes versus `product_status`.
- **Columns:**
  - `id uuid`
  - `product_id`
  - `old_effective_pub_date` (nullable)
  - `new_effective_pub_date` (NOT NULL)
  - `change_source text`
  - `engine_version`
  - `changed_at`
  - `metadata jsonb default '{}'`
- **Row counts by `change_source`:**
  - `initial_baseline` — 1,550
  - `shopify_pub_date` — 278
  - `override_date` — 51
- **No CHECK constraint** exists on `change_source`.
- **Indexes:** separate indexes on `product_id` and on `changed_at desc`. There is no
  composite index.
- **Schema drift:** no migration in `db/migrations/` creates this table. It was made
  out-of-band.
- **It already answers the test-title dates, with timestamps:**
  - Saint Peter: 10-13 → 09-22 (override, Jul 10).
  - Bordeaux: 10-13 → 10-27 (Jul 6) → 11-03 (Jul 16) → 10-20 (Aug 27).
  - Bordeaux's stray `11-03` tag is a date that was later reversed.

### 2.5 The `preorder` tag gates six predicates (E5)

`_has_preorder_tag` feeds each of the following:

1. `_is_structurally_preorder`, which feeds active, early-stock and delayed-import
2. `anomaly_stale_collection`
3. `anomaly_missing_tag`
4. `anomaly_missing_collection`
5. the `pdp_cleanup` path of early-stock
6. `_is_historical_preorder`

Every current preorder-family title carries the tag: 205 of 205.

### 2.6 Candidate DB signals for "was a preorder"

| Signal | Historical titles covered (of 155) | Non-preorder false positives |
|---|---|---|
| `release_state` | 135 | 0 |
| `lifecycle_snapshot` | 149 | 0 |
| Union of the two | 149 | 0 |
| `commitment_ledger` / `inventory_arrival` / `pubdate_history` | — | thousands |

- The six titles no clean signal covers are older releases from 2024–2025.
- `product_status` is overwritten on every upsert, so it holds no memory of a past
  status. Hence D5.

### 2.7 Wiring and consumers (E6, E7)

- **Input construction differs between callers.** The alignment audit builds
  `ClassificationInput` without `has_inventory_arrival`, so that field defaults to
  `False` there. Its expected status is therefore wrong for any title that has an
  arrival record.
- **The snapshot shape is defined twice:** in `orchestrator.py` and in
  `persistence.py`.
- **Three functions are named `reclassify_single_product`:**
  - `services/reclassification_service.py` — used by webhooks and `routes/reclassify`
  - `orchestrator.py`
  - root `reclassification.py` — used only by `override_service`
- **Leftover stub.** `classification/types.py` contains a stub
  `classify_preorder_product` that returns a placeholder status. Nothing imports it.
- **Importer.** The importer emits a Shopify **CSV**, not API calls, so it cannot
  write history rows.
  - It sets `custom.pub_date`.
  - It appends a `MM-DD-YYYY` tag (`edelweiss/parser.py:230–231`).
  - It writes a release-date sentence into the body HTML (`build_preorder_body`).
- **Storefront theme** (`main-product.liquid`, repo copy in `admin-dashboard`):
  - The visible "Published:" line reads `pub_date` only.
  - A separate block computes an effective date: the override if it is set and later
    than today, otherwise `pub_date`.
  - That block stamps **`properties[_pubdate]` onto every cart line item**.
  - Today, the delayed titles show one date on the page and put a different date on
    the order.
- **Stock-received signal** (the model for D6):
  - `vw_preorder_products.arrival_record_is_live` is a boolean derived in SQL.
  - It is counted as `vw_preorder_metrics.arrived_active_count`.
  - It is rendered as a `StockStatusPill` and an informational card in
    `PreorderSummaryCards.tsx`.
  - It is **not** a classification status.

### 2.8 Shopify platform facts (verified against shopify.dev)

- **API version.** The service defaults `SHOPIFY_API_VERSION=2025-10`, which is
  accessible until **2026-10-16 15:00 UTC**. After that, Shopify serves requests
  with the oldest accessible stable version. The latest stable version is `2026-07`.
- **`metafieldsSet`:**
  - At most 25 metafields per call.
  - Atomic.
  - Supports `compareDigest` compare-and-set.
- **Webhooks for metafield-only edits.** Whether a metafield-only edit emits
  `products/update` is not reliably documented, and community reports are
  inconsistent.
- **Events API.** Shopify's metafield-targeted **Events** triggers exist but remain
  `unstable` (developer preview). Track them; do not build on them.

### 2.9 Test baseline (`6677400`, local run)

- Result: **146 passed, 16 failed, 1 collection error.**
- **Classifier suites are healthy.** The single engine failure,
  `test_no_future_date_blocks_active`, is stale: it asserts the pre-delayed-import
  behavior.
- **At least 10 infra failures** come from fake Supabase clients that predate
  `.schema("preorder")`. These cover the orchestrator, persistence and pubdate
  history.
- **Undiagnosed failures:** lifecycle-snapshotter (4) and override-service (1).
- **Collection error:** `test_reclassify_endpoint.py` requires live Supabase env to
  import.
- **Missing dependencies:** `pytest` and `pytest-asyncio` are not in
  `requirements.txt`.

---

## 3. Target model

### 3.1 `pubdate_history` (extended)

**`change_source` vocabulary.** It is enforced by a new CHECK constraint that
includes the historical values, so existing rows stay valid.

| Value | Status | Meaning |
|---|---|---|
| `initial_baseline` | existing, kept | First classification with a date |
| `shopify_pub_date` | existing, **frozen** | Pre-phase `pub_date`-driven change. Not written after Move 1b. |
| `override_date` | existing, **frozen** | Pre-phase override-driven change. Not written after Move 1e. |
| `legacy_tag_fallback` | existing, never written | Kept only so the constraint admits the historical vocabulary |
| `ui_change` | new | Sanctioned dashboard edit, in either direction |
| `shopify_direct_edit` | new | Change seen via webhook or sweep with no matching dashboard intent |
| `override_migration` | new | Move 1d fold of an override value into `pub_date` |
| `tag_history_backfill` | new | Move 2b reconstruction from an archived tag |

**`metadata` keys.** These are documented and optional per source.

| Key | Used by | Meaning |
|---|---|---|
| `changed_by` | `ui_change` | Dashboard identity |
| `reason` | `ui_change` | Operator note |
| `direction` | `override_migration` | `earlier` \| `later` per D3. Two cases outside the ruling use extensions: `set` where no prior `pub_date` existed; the "equal" case writes no row. |
| `previous_pub_date` | `override_migration` | Prior `pub_date` metafield value |
| `previous_override` | `override_migration` | Prior override value |
| `effective_date_unchanged: true` | `override_migration` | The row records a change to the source-of-truth field, not to the effective date |
| `reconstructed: true` | `tag_history_backfill` | Timing is unknown; tags carry no timestamps |
| `raw_tag` | `tag_history_backfill` | The archived tag, as-is |
| `archive_id` | `tag_history_backfill` | Link to the archive row |
| `migration_run` | `override_migration`, `tag_history_backfill` | Run id, used for idempotency |

**Row shape for `override_migration`:**
- `old_effective_pub_date` holds the previous `pub_date` metafield value (null if
  there was none).
- `new_effective_pub_date` holds the folded value.

**Row shape for `tag_history_backfill`:**
- `old_effective_pub_date` is null.
- `new_effective_pub_date` is the archived date.
- `changed_at` is the time of the backfill run.

**Consumers must exclude `reconstructed` rows when determining the "current" date.**
Direct-edit detection compares against `product_status.effective_pub_date`, as the
orchestrator already does, never against the latest history row.

Add a composite index `(product_id, changed_at desc)`.

### 3.2 `preorder_since` (Move 3)

- **Column:** `preorder.product_status.preorder_since timestamptz null`.
- **Never cleared — enforced in the database, not only in app code.** A `BEFORE
  UPDATE` trigger sets `NEW.preorder_since := COALESCE(OLD.preorder_since,
  NEW.preorder_since)`. The existing full-row upsert in `persist_classification`
  therefore cannot erase it.
- **Seed (one-time):**
  - Read **live** Shopify tags. The snapshot can lag.
  - Every product carrying `preorder` gets `preorder_since` set to its earliest
    `pubdate_history.changed_at`, or to the seed-run time if it has no history.
  - Meaning of the value: "earliest time the system has evidence this was a
    preorder."
  - Log the run to a run-log table; guard G3 reads it.
- **Set on entry:** the orchestrator sets it whenever a result status is anything
  other than `not_a_preorder_product` and the column is null.
- **Engine input:** `ClassificationInput.was_preorder: bool`, populated by the
  shared input builder (Move 0.4). The engine stays pure.

### 3.3 Tag archive (Move 2)

`preorder.product_tag_archive` has these columns:

- `id bigserial`
- `product_id bigint not null`
- `tag text not null` — stored exactly as found, never repaired
- `tag_format text` — one of `mdy`, `ymd`, `malformed`
- `parsed_date date null` — null when malformed
- `archived_from text` — `shopify_live`
- `archive_run uuid`
- `archived_at timestamptz default now()`
- `unique (product_id, tag)`

Rules:

- **Date-shaped** means the tag matches `^\d{2}-\d{2}-\d{4}$` or
  `^\d{4}-\d{2}-\d{2}$`, whether or not the date is valid.
- The source is **live Shopify**, via `bulkOperationRunQuery`, because the strip
  operates on live Shopify.
- **The strip list is derived from the archive**, intersected with the tags present
  at strip time. Nothing is stripped that was not archived.

### 3.4 Missing-tag hygiene flag (D6, Move 3)

- **Engine:** the `anomaly_missing_tag` branch is removed. The title classifies to
  its real status.
- **`vw_preorder_products`:** add
  `missing_preorder_tag boolean = preorder_collection_present AND NOT
  preorder_tag_present`. Both inputs already exist in this view.
- **`vw_preorder_metrics`:** add `missing_preorder_tag_count`, mirroring
  `arrived_active_count`.
- **Single-source rule (PR #21):** if a list endpoint is ever added, it must select
  a view that defines the flag once. It must never re-derive the flag inline.
- **Dashboard:** a pill next to the status, as `StockStatusPill` does, plus an
  informational card like "Stock Received — Active". It is non-blocking and never a
  status.

### 3.5 Sanctioned write path (Move 1c)

The endpoints live in **preorder-service**. Per `CLAUDE.md`, the dashboard renders
and does not recompute.

**`POST /admin/preorders/{product_id}/pub-date`**

- Authenticated with `X-Admin-Token`.
- Body: `{ new_pub_date, reason, changed_by }`.
- Steps, in order:
  1. Read the current `custom.pub_date` value and its `compareDigest`.
  2. Insert a **write intent** row: `preorder.pubdate_write_intent(product_id,
     new_date, changed_by, reason, created_at, consumed_at)`.
  3. Call `metafieldsSet` with `compareDigest`. Take the metafield `type` from the
     live metafield definition; never hard-code it. On a digest mismatch, return 409
     so the operator can refresh.
  4. Reclassify synchronously via
     `services.reclassification_service.reclassify_single_product`, passing
     `change_source='ui_change'`.
  5. Return the refreshed row plus the new history row.

**Race safety:** when the orchestrator sees a date change that matches an
unconsumed intent (same product and date, recent), it labels the change `ui_change`
and consumes the intent. The webhook for the same edit then finds no diff and writes
nothing, whichever path commits first.

**`GET /admin/preorders/{product_id}/pub-date-history`** returns rows newest-first.
Reconstructed rows are flagged, and `override_migration` rows are labeled.

**Description warning:** the body HTML may contain an importer-written release-date
sentence. The editor surfaces a warning that the description may state the old date.
It does not auto-edit the body.

### 3.6 Direct-edit backstop (D9)

- **Webhook path:** `products/update` → reclassify. A date change with no matching
  intent is recorded as `shopify_direct_edit`. It is not rejected.
- **Scheduled sweep:** metafield-only edits may not emit `products/update` (§2.8), so
  a nightly reconciliation runs the unified alignment audit (Move 0.4) over
  preorder-family products. It reclassifies drift through the same orchestrator
  path, so drift is also recorded as `shopify_direct_edit`.
- **Events API:** revisit Shopify Events metafield triggers when they leave
  `unstable`.

---

## 4. Rollout

Each step is a separate PR against `main` unless noted. **Blocking gates (§5) are
preconditions, not checklist items.** A step with a gate may not start until the gate
is recorded as passed in the gate log (§5.1).

### Move 0 — Pre-flight (no behavior change)

- **0.1 Test infrastructure.**
  - Add `pytest` and `pytest-asyncio` to the dependencies.
  - Give the fake Supabase clients `.schema()`.
  - Make `test_reclassify_endpoint.py` importable without live env.
  - Rewrite `test_no_future_date_blocks_active` to assert delayed-import semantics.
  - Diagnose the lifecycle-snapshotter (4) and override-service (1) failures.
  - **Exit:** a green suite, or any remaining failures listed explicitly as known and
    unrelated. Move 1 edits the orchestrator, whose history logic has no working
    coverage today.
- **0.2 API version.** Set `SHOPIFY_API_VERSION=2026-07`, before 2026-10-16. Smoke-test
  `PRODUCT_FULL_QUERY` and `INVENTORY_ITEM_TO_PRODUCT_QUERY`.
- **0.3 Remove the stub.** Delete the stub `classify_preorder_product` from
  `classification/types.py`.
- **0.4 Shared input builder.** Add one `build_classification_input()` used by both
  the orchestrator and the alignment audit.
  - This fixes the audit's missing `has_inventory_arrival`.
  - The orchestrator's behavior is unchanged.
  - The audit's expected statuses change, and become correct.
- **0.5 Single snapshot shape.** Define the snapshot once, shared by the orchestrator
  and `persistence.py`.
- **0.6 Baseline migration.** Add a migration that captures the live
  `pubdate_history` DDL into `db/migrations/` as-is. This closes the schema drift.
- **0.7 G1 — Theme parity check (gate).** Compare the live theme's
  `main-product.liquid` with the repo copy for all `pub_date`, override and
  `_pubdate` logic. Record the result in the gate log.
- **0.8 G2 — `_pubdate` consumer inventory (starts now; gate at 1g).** Enumerate
  every consumer of the `_pubdate` line-item property: order tagging, email and
  notification templates, packing slips, other services and apps. Record the
  findings in the gate log.

### Move 1 — Single date plus override collapse

- **1a Migration.**
  - Add the `change_source` CHECK constraint (§3.1).
  - Add the composite index.
  - Create the `pubdate_write_intent` table.
- **1b Orchestrator.**
  - Accept `change_source` and `metadata` arguments.
  - Consume write intents.
  - Label webhook-seen changes `shopify_direct_edit`; stop writing
    `shopify_pub_date`.
  - Add tests covering intent-matched, unmatched and race orderings.
- **1c Endpoints.** Build the write and history endpoints (§3.5) with tests. Verify
  the `custom.pub_date` metafield definition type via the Admin API before coding
  the type into anything.
- **1d Fold migration.**
  - **Blocking precondition: G1 passed.**
  - Script: dry-run by default, idempotent (keyed on `metadata.migration_run`, and it
    skips a product whose `pub_date` already equals its override).
  - Before the run, capture every product's `effective_pub_date` and status to a
    snapshot table or file.
  - For each product with an override that differs from `pub_date`:
    `metafieldsSet` `pub_date := override`, and write an `override_migration` row
    (§3.1).
  - Products with no prior `pub_date` get `direction: set`.
  - The "equal" product gets no metafield write and no history row.
  - **Verify:**
    - Every product's `effective_pub_date` is unchanged from the pre-run snapshot.
    - The two test titles are no longer `anomaly_override_conflict`. After the
      fold, override equals `pub_date`, so Cases 1–3 cannot fire.
    - No other status changed.
- **1e Classifier drops override.**
  - **Blocking precondition: G4.**
  - Remove override from all of the following:
    - `resolve_effective_pub_date`
    - `ClassificationInput`
    - `ProductMetadata`
    - the shared builder
    - the snapshot
    - the alignment audit
    - override Cases 1–3
    - the `override_date is None` clause of `stale_collection`
  - Delete the override-conflict tests and update the rest.
  - **Shadow diff:** classify all preorder-family products with old and new engines.
    Accept only if there are zero status or effective-date changes.
- **1f Dashboard** (`admin-dashboard` repo).
  - Add a pub-date editor that calls 1c.
  - Add a read-only history panel that labels sources, the `override_migration` fold
    and reconstructed rows.
  - Show the description-HTML warning.
  - Remove `override_status` from the sidebar and types.
- **1g Retire override.**
  - **Blocking preconditions: G1 passed AND G2 passed.**
  - **Blocking order:** fold (1d) first, then verify all `_pubdate` consumers (G2),
    then delete.
  - Stop reading `preorder_override_date` in `shopify_service`.
  - Remove the override branch from the theme's effective-date block. After the
    fold this is dead logic, but remove it explicitly.
  - Delete the metafield values on all products. Verify the delete mutation against
    `2026-07` docs at build time. Optionally delete the metafield definition.
  - Rewrite `vw_preorder_products` and `vw_preorder_release_queue` without
    `override_status`.
  - Drop `preorder.product_overrides`.
  - Delete `override_service.py`, the root `reclassification.py`, and their tests.

**Move 1 acceptance:**
- Saint Peter (`7300376330373`) and Bordeaux (`7265304543365`) classify as
  `active_preorder` with no anomaly.
  - This holds on either side of 2026-09-22. After that date, Saint Peter is
    active via the delayed-import path: inventory −23, in collection, no arrival
    record.
- Each has an `override_migration` history row, and the panel shows it alongside
  their pre-existing timestamped history.
- No product reads `override_date` from any source.
- `product_overrides` is gone.

### Move 3 — DB-derived preorder identity (before Move 2)

- **3a Migration.** Add the `preorder_since` column and the never-clear trigger
  (§3.2), plus the seed run-log table.
- **3b Seed script.** It reads live tags, runs once, logs to the run-log, and is
  idempotent because the trigger keeps the first value. **Verify:** every product
  carrying `preorder` has a non-null `preorder_since`.
- **3c Orchestrator.** Set `preorder_since` on preorder entry. The shared builder
  populates `was_preorder`.
- **3d Engine: per-predicate rulings.** These are proposed; confirm them at Move 3
  kickoff.

| # | Predicate | Today | Proposed |
|---|---|---|---|
| 1 | `_is_structurally_preorder` | tag OR collection | `was_preorder` OR collection |
| 2 | `anomaly_stale_collection` | requires tag | requires `was_preorder` |
| 3 | `anomaly_missing_tag` | terminal status | **removed** → hygiene flag (D6, §3.4) |
| 4 | `anomaly_missing_collection` | requires tag | requires `was_preorder` |
| 5 | `pdp_cleanup` | requires tag | requires `was_preorder` |
| 6 | `_is_historical_preorder` | requires tag | requires `was_preorder` |

  **Shadow diff:** zero status changes for current products. None are
  `anomaly_missing_tag` today.
- **3e Hygiene flag.** Add the view and metrics columns (§3.4) and the dashboard
  pill and card.
- **3f Tests.**
  - A historical title with the tag removed stays `historical_preorder`.
  - A never-preorder product stays `not_a_preorder_product`.
  - An in-collection title without the tag gets its real status and
    `missing_preorder_tag = true`.
  - The trigger refuses to clear `preorder_since`.

### Move 2 — Date tags (last)

- **2a Archive.** Create the archive table migration (§3.3) and an archive script
  that reads live Shopify tags via `bulkOperationRunQuery`.
  - It archives every date-shaped tag as-is, malformed tags included.
  - **Verify:** the archive row count equals the date-shaped tag count in a fresh
    live read.
- **2b Selective backfill.** For products with a non-null `preorder_since`, write a
  `tag_history_backfill` row (§3.1) for each archived, parseable date that is not
  already present in history as an old or new date. There were 5 such dates as of
  2026-09-19. Backlist dates stay in the archive.
- **2c Remove the date-tag code.** This is inert (E2).
  - `DATE_TAG_RE`
  - `_extract_date_tags_raw`
  - `date_tags_raw`
  - `parsed_date_tags`
  - `DATE_TAG_PATTERN`
  - `ClassificationInput.date_tags`
  - the resolution branch
  - `anomaly_pubdate_conflict`
  - `anomaly_multi_date_conflict`
  - the date clause of `stale_collection`
  - the related tests

  **Shadow diff:** zero changes.
- **2d Strip.** **Blocking precondition: G3, enforced in the script.**
  - The strip list comes from the archive (§3.3).
  - Run it in batches with `bulkOperationRunMutation`. Verify the tag-removal
    mutation and its bulk support against `2026-07` docs at build time.
  - **Webhook load plan:** about 9,100 `products/update` deliveries, each
    reclassifying synchronously. Coordinate with the webhook-gateway owner, batch the
    work, and run it off-hours.
  - Keep the `preorder` tag and every non-date tag.
- **2e Importer.**
  - Stop appending `pub_date_tag` (`edelweiss/parser.py:230–231`).
  - Update `preorder-bulk-importer/docs/canonical-map.md`.
  - New products get history through the existing `initial_baseline` path.

**Move 2 acceptance:**
- No product carries a date-shaped tag.
- Every stripped tag exists in the archive.
- Preorder-family history includes the backfilled dates.
- The classifier reads only `pub_date`.

---

## 5. Blocking gates

- **G1 — Live theme parity.**
  - *Set at:* Move 0.7.
  - *Blocks:* 1d and 1g.
  - The live theme's `pub_date`, override and `_pubdate` logic must match the repo
    copy.
  - If it diverges, the "fold then delete is safe" guarantee is void until it is
    re-verified against the live theme.
- **G2 — `_pubdate` consumers verified.**
  - *Set at:* Move 0.8.
  - *Blocks:* 1g, the override metafield deletion.
  - Every consumer of `properties[_pubdate]` is enumerated and confirmed to be
    unaffected by deleting the override. The required order is **fold → verify
    consumers → delete**. Deletion may never precede this verification.
- **G3 — Seed and archive before strip. Enforced in code.**
  - *Blocks:* 2d.
  - The strip script **refuses to run** and exits non-zero, with **no override flag**,
    unless all of the following hold:
    1. The archive is non-empty: `select count(*) from preorder.product_tag_archive`
       is greater than 0.
    2. The seed has run: the `preorder_since` seed run-log has a completed run, and
       at least one `product_status.preorder_since` is not null.
    3. Per product, every tag to be removed exists in the archive for that product.
    4. Per product, any product whose live tags include `preorder` has a non-null
       `preorder_since`.
  - Checks 3 and 4 run at strip time, for each batch, against live state.
- **G4 — Fold verified before the classifier drops override.**
  - *Blocks:* 1e.
  - 1d's verification must have passed in production: effective dates unchanged, and
    the test titles cleared.
  - Deploying 1e before the fold would silently change the effective date of every
    override product:
    - the 29 forward-moved products revert to their stale, earlier `pub_date`
    - the 2 test titles revert to their later `pub_date`
    - the 4 no-`pub_date` products lose their date entirely

### 5.1 Gate log

| Gate | Passed on | By | Evidence |
|---|---|---|---|
| G1 | | | |
| G2 | | | |
| G3 | enforced in code (2d) | — | — |
| G4 | | | |

---

## 6. Open items (facts to establish; not decisions)

1. **G1 and G2 evidence.** Live theme parity, and the inventory of `_pubdate`
   consumers.
2. **Webhook-gateway configuration.** Does the gateway's `products/update`
   subscription fire on metafield-only edits? This sizes how much the §3.6 sweep is
   exercised. It does not change the design.
3. **Metafield definition type.** The `custom.pub_date` definition type, to be read
   via the Admin API at 1c.
4. **Undiagnosed test failures.** Lifecycle-snapshotter (4) and override-service (1),
   from Move 0.1.
5. **Stale description dates.** Whether to eventually rewrite the importer-written
   release-date sentence in descriptions. Out of scope; the editor warns (§3.5).

## 7. Guardrails

These are carried forward from the original.

- Collection membership stays authoritative for live-preorder identity.
- The classifier stays a pure, tested function. Every engine change ships with tests
  and a shadow diff. Update `docs/test_matrix.md` as it is touched.
- No history is destroyed. The archive precedes the strip, and the fold precedes
  deletion. `pubdate_history` rows are never rewritten.
- The `preorder` tag is demoted, not deleted.
- Do not weaken to streamline. Each removed signal is replaced by a stronger
  DB-owned one.
