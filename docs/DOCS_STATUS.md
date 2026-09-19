# Status of documents in `docs/` (and repo-level docs)

Handoffs and reference docs accumulate, and a confident claim inherited from a
stale document costs real time. This file records which documents are current,
which are unaudited, and which contain known errors — plus **live code
landmines** discovered while auditing docs.

**Check this file before trusting anything else in `docs/` or the root
`README.md`.**

A row marked **Not yet re-verified** means exactly that: nobody has checked it
against the live system in this pass. It is not an endorsement. Prefer reading
the code (`shopify_token.py`, `classification/engine.py`, the live Supabase
views) over trusting an unaudited doc.

Last updated: 2026-09-19

| Document | Status |
|---|---|
| `docs/DOCS_STATUS.md` (this file) | **Authoritative** for document status only. |
| `docs/shipping_profiles.md` | **Current and authoritative** for the date-based shipping-profile create/repurpose flow (zones, carrier IDs, `includeAllProvinces`). Written and verified this engagement. Its Auth section correctly states client-credentials. |
| `docs/phase_unified_pubdate_and_tag_simplification.md` | **SUPERSEDED (2026-09-19) by `docs/phase_unified_pubdate_rev2.md`.** Kept for the trail — do NOT build from it. Its premises carry errata **E1–E7** (section below): it proposed a duplicate history table, assumed live date tags, missed the DB override source, rested on a false "override always means earlier" premise, understated the `preorder` tag's coupling, omitted several engine/wiring touch-points, and did not account for Shopify-side and storefront consumers. |
| `docs/phase_unified_pubdate_rev2.md` | **CURRENT — authoritative plan for the unified pub-date / override-collapse / tag-simplification phase. Not yet implemented.** §2 (current state) verified 2026-09-19 against `main` `6677400`, the production `preorder` schema, `admin-dashboard` `ac7609a`, and shopify.dev; everything else describes FUTURE state. Contains the locked decisions D1–D10 and blocking gates G1–G4 (G3 enforced in code). Record gate sign-offs in its §5.1 gate log. |
| `docs/Preorder Classification Specification.md` | **Not yet re-verified.** The engine has changed since it was written (added `anomaly_stale_collection`, delayed-import hold, `>=`/`<=` pub-date boundaries, `has_inventory_arrival` gates). Treat `classification/engine.py` as truth; audit this doc against it before relying on it. |
| `docs/Trust_Tier_Labeling.md` | **Not yet re-verified.** Referenced by data-confidence logic; may predate several arrival/reporting changes. |
| `docs/test_matrix.md` | **Not yet re-verified.** |
| root `README.md` (54 KB) | **Not yet re-verified — audit before trusting.** Large inherited document; has not been read against current state in this pass. Do not treat its setup/env sections as authoritative until audited — see the auth contract and landmines below. |

---

## Where key logic lives (verified — safe to trust)

Facts about *where the truth is computed*, recorded so the next thread doesn't
re-derive or duplicate them.

- **Alert count and list share one source-of-truth view each** (as of PR #21,
  2026-09-19). `preorder.vw_late_arrivals_unresolved` and
  `preorder.vw_no_arrival_unresolved` encode each alert's full definition
  (filters + `alert_dismissals` exclusion) once. `vw_preorder_metrics` counts
  these views; the `/late-arrivals` and `/no-arrival-titles` endpoints select
  them. To change alert logic, edit the view — never re-derive it inline in the
  metric or in the endpoint. (This retired a recurring class of count/list drift
  bugs; do not reintroduce parallel derivations.)
- **Pub-date history already exists** (verified 2026-09-19):
  `preorder.pubdate_history`, written by
  `orchestrator.classify_and_persist_product` whenever the effective pub date
  changes versus `product_status`. Live since 2026-03-03. Extend it — do not
  create a parallel history table (see E1).

---

## Errata — `phase_unified_pubdate_and_tag_simplification.md` (superseded)

The seven premise corrections below were found while verifying the proposal
against live code, the production database, and shopify.dev on 2026-09-19. They
are why the proposal was superseded by `docs/phase_unified_pubdate_rev2.md`,
which is built on the corrected facts. Counts are 2026-09-19 snapshots.

- **E1 — The history table already exists** (§3.2 and Move 1.1 of the original).
  The proposal would create `pub_date_history`. But `preorder.pubdate_history`
  is already live: about 1,880 rows since 2026-03-03, written by the orchestrator
  on every effective-date change. It has a `metadata jsonb` column. Resolution:
  extend the existing table (D2).
- **E2 — Date tags are inert on the live path** (§2.1, §2.3, §2.6). The
  proposal says the classifier reads `MM-DD-YYYY` tags. In reality:
  - `shopify_service` keeps only `YYYY-MM-DD` tags.
  - `ProductMetadata.parsed_date_tags()` keeps only `MM-DD-YYYY` tags.
  - No tag passes both, so `date_tags` is always `[]` in production.
  - There are zero `legacy_tag_fallback` history rows.
  - `anomaly_pubdate_conflict`, `anomaly_multi_date_conflict` and override Case 2
    can never fire.
  - On products: 9,171 valid `MM-DD-YYYY` tags, 9 valid `YYYY-MM-DD` tags, and 2
    malformed tags (`02-29-2022`, `13-02-2020`).
  - Do not "fix" the regex. It would activate `pubdate_conflict` at scale.
- **E3 — Override has two sources** (§1, §2.6). The proposal treats override as
  a metafield only. In reality:
  - `orchestrator` and the alignment audit also read
    `preorder.product_overrides` via `override_service.fetch_override_date`, and
    the DB value **wins**.
  - The table holds one junk row (product `12345`).
  - Its write path, `update_override_date_and_reclassify`, is called by no route
    and writes a column (`updated_by`) the table lacks.
  - `vw_preorder_products.override_status` joins it, and the dashboard sidebar
    renders that column.
  - The metafield is `custom.preorder_override_date`.
- **E4 — "An override always means an earlier date" is false** (§1.1). Of 36
  products with the override metafield:
  - 2 are earlier: the two test titles.
  - 29 are later: 22 historical, 6 active, 1 early stock.
  - 4 have no `pub_date`.
  - 1 is equal.

  The `ui_backdate` migration label built on this premise is retired in favor of
  `override_migration`, with the direction in metadata (D3). Effective dates
  already equal the override value for all 36, so folding is
  effective-date-neutral.
- **E5 — The `preorder` tag gates six predicates, not one** (§2.4). The proposal
  says the tag only gates `historical_preorder`. `_has_preorder_tag` also feeds:
  - `_is_structurally_preorder` (active, early-stock, delayed-import)
  - `anomaly_stale_collection`
  - `anomaly_missing_tag`
  - `anomaly_missing_collection`
  - the `pdp_cleanup` early-stock path

  Every one needs a ruling in Move 3.
- **E6 — The engine and wiring touch-list is incomplete** (§2.3, §2.5, §2.6).
  - `anomaly_stale_collection` also reads `override_date` and `date_tags`.
  - `classification/types.py` holds a stub `classify_preorder_product`.
  - The orchestrator and the alignment audit do **not** build
    `ClassificationInput` the same way: the audit omits `has_inventory_arrival`.
  - Three different functions are named `reclassify_single_product`.
  - The bulk importer emits a Shopify CSV. It cannot write history rows, and it
    also bakes a release-date sentence into the description HTML.
- **E7 — Shopify-side and storefront consumers were not accounted for** (§2.5,
  §7).
  - The pinned API version `2025-10` stops being accessible on 2026-10-16
    15:00 UTC.
  - Metafield-only edits are not reliably documented to emit `products/update`.
    Shopify's metafield-targeted Events are still `unstable` preview.
  - The storefront theme (`main-product.liquid`) reads the override metafield to
    compute an effective date. It stamps that date on every cart line as
    `properties[_pubdate]`.

  This makes fold-before-delete order load-bearing for a customer-facing
  consumer. See gates G1 and G2 in the revision.

---

## The authoritative auth contract (verified against `shopify_token.py`)

This is the one thing that has bitten multiple threads, so it is stated here as
the reference:

- Shopify Admin API auth is the **OAuth client-credentials grant**. Required env:
  `SHOP_URL`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`.
- `SHOPIFY_ACCESS_TOKEN` is **retired** and read by nothing in the Python
  services. Any doc, workflow, or script that references it as the auth
  mechanism is stale.
- Two entry points, same grant (see `shopify_token.py`):
  - **Async** callers → `get_token_manager()` / the canonical `ShopifyClient`
    in `shopify_client.py` (token injection, 401 refresh-retry, backoff).
  - **Sync** scripts / sync routes → `get_token_sync()` (returns a short-lived
    token; put it in the `X-Shopify-Access-Token` header).
- The version env var is **`SHOPIFY_API_VERSION`** (default `2025-10`). The old
  name `API_VERSION` is superseded; a file reading `API_VERSION` may silently
  drift to a different default.
- `SHOP_URL` must be the `.myshopify.com` host (e.g. `castironbooks.myshopify.com`),
  not the custom domain. Use `normalize_domain()` from `shopify_token.py`.

---

## Live code landmines (not docs — actual stale references in the repo)

Found via `grep SHOPIFY_ACCESS_TOKEN` across the repo while building this file.
These are **active**, not just documentation errors.

### Landmine 1: `.github/workflows/weekly_release_engine.yml` passes retired secrets

The workflow injects `SHOPIFY_ACCESS_TOKEN` and `API_VERSION` into
`weekly_release_engine.py`. But that script now authenticates via
`get_token_sync()` (client-credentials) and reads `SHOPIFY_API_VERSION`. So the
workflow supplies a retired token var and the wrong version var name. If this
workflow still runs on schedule it is either failing or running on stale/absent
config. **Needs:** replace `SHOPIFY_ACCESS_TOKEN` with `SHOPIFY_CLIENT_ID` +
`SHOPIFY_CLIENT_SECRET`, and `API_VERSION` with `SHOPIFY_API_VERSION`, in the
workflow's env block (and add the corresponding GitHub Actions secrets).
Not fixed here — flagged for a dedicated change so it can be tested.

### Landmine 2: `audit_preorder_product.js` (Node) reads retired env

Reads `process.env.SHOPIFY_ACCESS_TOKEN` and `SUPABASE_SERVICE_KEY` (the old
Supabase key name; standard is `SUPABASE_SERVICE_ROLE_KEY`). Node was the
deferred track in the token migration. This script will not authenticate as-is.
**Needs:** the Node token-factory pattern (client-credentials) noted in the
migration runbook, or retirement if unused.

### Landmine 3: pinned Shopify API version is about to become inaccessible

`SHOPIFY_API_VERSION` defaults to `2025-10`, which is accessible until
**2026-10-16 15:00 UTC**. After that, Shopify serves requests with the oldest
accessible stable version. The latest stable version is `2026-07`. **Needs:** a
version bump plus a smoke test. This is scheduled as rev2 Move 0.2.

### Landmine 4: the alignment audit does not mirror the orchestrator

`audits/shopify_alignment_audit.py` builds `ClassificationInput` without
`has_inventory_arrival`, so the field defaults to `False`. Its "expected" status
is therefore wrong for any title with an arrival record, including early stock,
stale collection, PDP cleanup and delayed import. **Needs:** a shared input
builder, scheduled as rev2 Move 0.4.

### Landmine 5: stub classifier in `classification/types.py`

The file defines a second `classify_preorder_product` that returns a placeholder
`anomaly_missing_tag`. Nothing imports it today. Importing the wrong one would
silently misclassify everything. **Needs:** deletion, scheduled as rev2 Move 0.3.

### Landmine 6: dead, broken override write path

`override_service.update_override_date_and_reclassify` has no route. If it were
called, it would fail, because it upserts `updated_by`, a column
`preorder.product_overrides` does not have. The table holds one junk row
(product `12345`). **Needs:** removal with the table, scheduled as rev2
Move 1g.

### Landmine 7: `pubdate_history` exists only in production

No migration in `db/migrations/` creates `preorder.pubdate_history`. It was made
out-of-band. **Needs:** a baseline migration capturing the live DDL, scheduled
as rev2 Move 0.6.

### Landmine 9: `/approvals` router is mounted with NO authentication

`routes/approvals.py` is included in `main.py` with no auth dependency. The only
app-wide middleware is CORS, which restricts browsers, not scripts or `curl`.

Its endpoints:
- `GET /approvals/list` reads `preorder.vw_pending_approvals`.
- `POST /approvals/approve` **writes** `preorder.approvals`, including an
  `override_pub_date` column.
- `POST /approvals/revoke` **writes** `preorder.approvals`.

Found from code on 2026-09-19. It was deliberately not probed against
production. No callers exist in this repo or in `admin-dashboard`, and the table
is dormant (1 row, last updated 2025-11-10).

**Needs:** an owner decision, then a dedicated PR. The likely fix is to unmount
the router; the alternative is to add the `admin_*` `require_admin_token`. It is
not part of the pub-date phase. Its `override_pub_date` column feeds only
`vw_pending_approvals`, not the classifier.

### Landmine 10: import-time env reads, and admin-auth outliers

**Import-time env reads.** Five routers call `create_client(...)` at import
instead of using the lazy `services/supabase_client.get_client()`:
- `admin_preorders`
- `admin_nyt`
- `admin_shipping`
- `admin_tagger`
- `admin_cleanup`

Four jobs read required env with `os.environ[...]` at module level:
- `mailtrap`
- `order_tagger`
- `nyt_notifier`
- `nyt_reporter`

As a result, `import main` needs a full environment. Tests accommodate this in
`tests/conftest.py` (PR #25). Production is unchanged.

**Admin auth outliers.** The five `admin_*` routers are consistent:
`X-Admin-Token` checked against `PREORDER_ADMIN_TOKEN`, matching `CLAUDE.md`.
Each keeps its own copy of `require_admin_token`. The outliers are:
- `internal_events` uses `x-admin-key` checked against `PREORDER_ADMIN_TOKEN`.
- `reclassify` uses `x-admin-key` checked against `RECLASSIFY_ADMIN_KEY`.

**For rev2 Move 1c:** the new pub-date endpoints follow the `admin_*`
convention.

**Needs:** a separate cleanup: lazy clients and a single shared admin-auth
dependency. It is not blocking.

### Resolved

#### Landmine 8: test suite baseline is red — RESOLVED in PR #25 (rev2 Move 0.1)

The suite went from 146 passed / 16 failed / 1 error to **178 passed / 1 skipped**.

What PR #25 fixed:
- **Shared test fake.** It added the shared in-memory `tests/fakes.py`
  (`FakeSupabase`) that mirrors `.schema().table()`. Tests now assert DB state.
- **Snapshotter fake.** It rewrote the lifecycle-snapshotter fake to model the
  current queries.
- **Stale test.** It replaced the stale delayed-import test.
- **Dead-code test.** It skipped the dead override-service test with a reason
  (Landmine 6).
- **Dependencies.** It added `requirements-dev.txt`.
- **Two silent traps:**
  - `.gitignore` excluded all of `tests/`, so new test files were never
    committed.
  - `tests/test_inventory_arrival_replay.py` had a trailing space in its name,
    so pytest never collected it.
- **Diagnosis of the 5 previously undiagnosed failures:**
  - The 4 lifecycle-snapshotter failures were fake drift. One of those tests
    also asserted behavior production had removed.
  - The override-service failure was call-style drift on dead code.

The original entry is preserved below.

##### (original) Landmine 8: test suite baseline is red

A local run at `6677400` gave 146 passed, 16 failed, 1 collection error.

- At least 10 failures are fake Supabase clients that lack `.schema()`
  (orchestrator, persistence, `test_pubdate_history`).
- 1 failure is a stale test, `test_no_future_date_blocks_active`, which asserts
  pre-delayed-import behavior.
- 5 failures are undiagnosed: lifecycle-snapshotter (4) and override-service (1).
- `test_reclassify_endpoint.py` needs live Supabase env to import.
- `pytest` and `pytest-asyncio` are not in `requirements.txt`.

**Needs:** rev2 Move 0.1, before any orchestrator change.

---

## Correct references (verified accurate — safe to trust)

For contrast, these correctly describe the current auth and were confirmed
during the audit:

- `shopify_token.py` — the source of truth for auth.
- `shopify_client.py` — "this client never reads SHOPIFY_ACCESS_TOKEN."
- `jobs/order_tagger.py` — "no longer reads SHOPIFY_ACCESS_TOKEN."
- `docs/shipping_profiles.md` Auth section.

---

## How to use / extend this file

- Before trusting a doc, find its row. If it says **Not yet re-verified**, read
  the relevant code instead, then optionally upgrade the row with a real status
  and any errata.
- Record errata here rather than editing them into a large handoff inline —
  patching one paragraph of a big file means rewriting the whole file, which is
  the truncated-write hazard. Small, additive status notes here are safer.
- When you fix a landmine, move its entry to a "Resolved" note with the commit/PR
  rather than deleting it, so the history of the trap is preserved.
