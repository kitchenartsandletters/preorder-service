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

Last updated: 2026-09-21

| Document | Status |
|---|---|
| `docs/DOCS_STATUS.md` (this file) | **Authoritative** for document status only. |
| `docs/shipping_profiles.md` | **Current and authoritative** for the date-based shipping-profile create/repurpose flow (zones, carrier IDs, `includeAllProvinces`). Written and verified this engagement. Its Auth section correctly states client-credentials. |
| `docs/phase_unified_pubdate_and_tag_simplification.md` | **SUPERSEDED (2026-09-19) by `docs/phase_unified_pubdate_rev2.md`.** Kept for the trail — do NOT build from it. Its premises carry errata **E1–E7** (section below): it proposed a duplicate history table, assumed live date tags, missed the DB override source, rested on a false "override always means earlier" premise, understated the `preorder` tag's coupling, omitted several engine/wiring touch-points, and did not account for Shopify-side and storefront consumers. |
| `docs/phase_unified_pubdate_rev2.md` | **CURRENT — authoritative plan for the unified pub-date / override-collapse / tag-simplification phase. Not yet implemented.** §2 (current state) verified 2026-09-19 against `main` `6677400`, the production `preorder` schema, `admin-dashboard` `ac7609a`, and shopify.dev; everything else describes FUTURE state. Contains the locked decisions D1–D10 and blocking gates G1–G4 (G3 enforced in code). Record gate sign-offs in its §5.1 gate log. **Amended 2026-09-21 by A1–A2** (section below): new blocking gate **G5** before any bulk product edit, and the nightly reconciliation sweep becomes required. |
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

## Amendments to `phase_unified_pubdate_rev2.md`

These amendments are recorded here, per this file's convention of not
rewriting large docs inline. They are binding on the rev2 plan. They come from
the webhook-gateway investigation (Incident I1, below).

- **A1 — New blocking gate G5: webhook ingestion must survive a bulk edit.**
  - *Blocks:* rev2 step 2d (the date-tag strip, about 9,100 `products/update`
    events) and any other bulk product edit made by this phase.
  - *Evidence:* on 2026-08-29 a bulk edit of about 6,000 products produced
    about 22,000 `products/update` events.
    - preorder-service answered with Railway edge 502 and 429 responses.
    - The gateway gave up on 22,178 of them and never retried.
  - *Pass condition, at least one of:*
    - (a) the preorder-service webhook route acknowledges immediately and does
      the reclassification work asynchronously; or
    - (b) the strip is throttled to a rate proven safe in a pilot batch.
  - *Required in both cases:* a pilot batch shows **zero failed deliveries**
    to preorder-service in `public.external_deliveries`.
  - *Enforced in code:* extend the G3 guard in the strip script. After each
    batch, abort if `public.external_deliveries` has any `failed` row for the
    preorder-service target since the batch started.
- **A2 — The nightly reconciliation sweep is required, not optional.**
  - Webhook delivery to preorder-service is **not reliable**: 22,890 events
    were lost between 2026-04-27 and 2026-09-11, and none was replayed.
  - The sweep planned in rev2 §3.6 is therefore a correctness requirement for
    Move 1.
  - Direct-edit detection must not depend on webhooks alone.

---

## Incidents and data repairs

### I1 — webhook-gateway delivery failures (investigated 2026-09-21)

**Symptom.** `webhook-gateway` has **6,200 open GitHub issues**, all titled
"External Delivery Failure: <topic>". They are automated: one issue per
delivery that failed after retries.

**Source data.** `public.external_deliveries` and `public.webhook_logs` in the
webhook-gateway Supabase project, which holds the payloads.

**Failed deliveries by target**, all-time as of 2026-09-21:

| Target | Failed | Succeeded |
|---|---|---|
| preorder-service | 22,890 (2026-04-27 → 2026-09-11) | 121,546 |
| backorder-service | 53,081 (2026-06-18 → 2026-09-15) | 65,457 |
| used-books-service | 42 (ends 2026-06-17) | 43,021 |

**preorder-service failures by episode:**

| Date | Failed events |
|---|---|
| 2026-08-29 | ~22,180: 13,867 × 502 `upstream error`, 8,075 × 429 `rate limited`, 145 connection failures, 91 × 503 |
| 2026-05-10 → 05-11 | 672 |
| 2026-06-17 | 21 |
| 2026-06-08 | 7 |
| 2026-09-11 | 6 |

The response bodies come from Railway's edge, not the app.

**Cause of the 2026-08-29 burst:** a bulk edit touching about 6,000 products at
around 21:24 ET on 2026-08-28. The owner believes the source is
`supply-chain-service`; this is **not yet verified** against its commit
history.

**Impact on preorder data:**
- **Product classification: no lasting harm.**
  - 5,985 products lost at least one `products/update`.
  - 5,845 have been reclassified since, by a later update.
  - The remaining 140 are genuine non-preorders; none of their lost payloads
    carried the `preorder` tag.
- **Commitment ledger: 13 preorder orders lost events.** 11 needed repair; the
  other 2 (#80305, #80642) were already complete.
  - Mediterranean All the Way (active preorder, pub 2026-10-20): 3 open
    commitments were missing entirely.
  - 7 historical titles each had a line netting −1: the fulfillment was
    recorded but not the creation.
  - #80284 (Oteque) lost its create event, and its fulfillment was also
    unrecorded (see Landmine 14).
- **None of the 22,890 failed events was ever recovered.** No later success
  exists for the same event, and none was replayed.

**Resolution:**
- Ledger repaired: see R1.
- Gate G5 added: see A1.
- Gateway root causes recorded as Landmine 13.
- The 6,200 alert issues can be bulk-closed now that they are recorded here.
  That is owner-approved, and belongs to the webhook-gateway repo.

### R1 — ledger repair for 10 single-line orders (executed 2026-09-21)

**What was done.**
- 10 rows were inserted into `preorder.tracking` at 2026-09-21 19:23:54 UTC,
  one per lost `orders/create` event.
- Each was rebuilt from the original payload in `public.webhook_logs`, in the
  same shape the webhook handler writes: fresh `event_id`,
  `source_service='gateway'`, status `pending`.
- Headers carry `X-Repair-Source-Gateway-Event` = the original gateway event.
- `processing_notes` starts with `repair 2026-09-21:`.
- The ledger builder (a 5-minute cron) derived the ledger rows on its
  19:25 UTC run.

**Why it is safe to re-run.**
- The insert was atomic, with guards: exactly 10 source orders, each
  single-line, and no pre-existing tracking row.
- The ledger's own constraints block duplicates, notably
  `commitment_positive_once (order_id, line_item_id)`.

**Result, verified per line:**
- **Mediterranean All the Way:** #80289, #80292 and #80303 each now net +1,
  as open commitments.
- **Historical lines, each now netting 0:**
  - #80281 The Great Book of Chocolate
  - #80288 Eat (Like) the Rich
  - #80291 The Hot Dog Cookbook
  - #80295 Ammazza!
  - #80296 Jacques Pépin Complete Techniques
  - #80297 Beyond Peaks
  - #80300 The Noma Guide to Building Flavour
- The repaired rows carry the original order dates (2026-05-10/11), so they
  count in the correct presale windows.

**Find the repair rows:**

```sql
select * from preorder.tracking where processing_notes like 'repair 2026-09-21:%';
```

**Outstanding:**
1. **#80284 (Oteque, 7-line order)** is deliberately **not** repaired yet.
   - Its Oteque line shipped, but the fulfillment is also missing
     (Landmine 14).
   - Adding only the +1 would create a false open commitment.
   - Fix it together with Landmine 14.
2. **Frozen lifecycle snapshots:** the 7 historical titles' presale totals are
   still one short each. Snapshots are write-once, and none were modified.
   Decide on recomputation with Landmine 14, which affects the same snapshots.

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
- The version resolves in exactly one place: **`shopify_version.get_api_version()`**.
  It reads `SHOPIFY_API_VERSION`, then the legacy `API_VERSION`, then the code
  default (`2026-07` since rev2 Move 0.2). `tests/test_shopify_api_version.py`
  fails if any other Python file hard-codes a version or reads these env vars.
  In production, the `SHOPIFY_API_VERSION` variable on each Railway service
  **overrides** the code default.
- `SHOP_URL` must be the `.myshopify.com` host (e.g. `castironbooks.myshopify.com`),
  not the custom domain. Use `normalize_domain()` from `shopify_token.py`.

---

## Live code landmines (not docs — actual stale references in the repo)

Found via `grep SHOPIFY_ACCESS_TOKEN` across the repo while building this file.
These are **active**, not just documentation errors.

### Landmine 1: dead weekly-report workflow and scripts (retire, do not repair)

**Owner-confirmed 2026-09-19:** the weekly report is an earlier phase of this
service that was allowed to decay. It is not produced.

The pieces:
- `.github/workflows/weekly_release_engine.yml` (Sundays 10:15 UTC) runs the
  root `weekly_release_engine.py --mark-reported`.
- It passes the retired `SHOPIFY_ACCESS_TOKEN` and the legacy `API_VERSION`
  secret. It does not pass the `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET`
  that `get_token_sync()` requires.

GitHub Actions history shows **all 13 recorded runs failed**, weekly from
2026-03-22 to 2026-06-14, with **no runs since**. The step-level error could
not be read (rate-limited), and neither could whether the workflow is now
disabled.

`services/weekly_release_engine.py` is part of the same dead phase. Nothing
imports it; two comments reference its logic.

**Not affected:** `preorder.release_state` is still written by the live flow:
- `routes/admin_preorders.py` upserts it.
- `routes/admin_nyt.py` and `jobs/nyt_reporter.py` set `nyt_uploaded_at`.

Last write was 2026-09-14. Views reading it: `vw_preorder_products`
(`released_to_reporting`), `vw_candidate_release_base`,
`vw_reportable_preorders`.

**Needs:** a dedicated cleanup PR that retires the workflow and both
weekly-engine scripts. Confirm each is unreferenced first. Deleting the
workflow file prevents it from being re-enabled by accident.

### Landmine 2: `audit_preorder_product.js` (Node) reads retired env

Reads `process.env.SHOPIFY_ACCESS_TOKEN` and `SUPABASE_SERVICE_KEY` (the old
Supabase key name; standard is `SUPABASE_SERVICE_ROLE_KEY`). Node was the
deferred track in the token migration. This script will not authenticate as-is.
It also hard-codes Admin API version `2025-01`, which is outside Shopify's
support window. As a `.js` file it is not covered by the Python version guard.
**Needs:** the Node token-factory pattern (client-credentials) noted in the
migration runbook, or retirement if unused.

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

### Landmine 13: webhook delivery to preorder-service is lossy (found 2026-09-21)

See Incident I1 for the evidence.

**Contributing causes:**
- **preorder-service does the work inside the request.**
  `routes/webhooks.py::_handle` awaits the full reclassification: two Shopify
  fetches, classification and DB writes, before responding. Under a burst,
  Railway's edge returns 502 and 429.
- **The gateway does not retry effectively or replay.**
  - `external_deliveries.attempt_count` is always 0, although the issue text
    says "Attempt: 3".
  - No failed delivery has ever been replayed.
- **The gateway sends an empty `X-Gateway-Event-ID`.**
  - The handler falls back to a random UUID.
  - So preorder-service cannot recognize a redelivered webhook.
  - Only ledger constraints prevent double-counting.
- **The gateway opens one GitHub issue per failed delivery.** That produced
  6,200 open issues.

**Needs:**
- In preorder-service: fast-acknowledge the webhook and process it
  asynchronously. This is gate G5, A1.
- In webhook-gateway, a separate repo: working retries and replay, a real
  event ID header, and aggregated alerting instead of one issue per failure.

### Landmine 14: multi-line fulfillments under-recorded in the commitment ledger (found 2026-09-21)

**Mechanism:**
- For `orders/create`, the webhook handler writes **one tracking row per line
  item**.
- For `orders/fulfilled`, `_extract_order_facts` returns **a single row for
  the whole order**.
- The ledger builder then derives a −row for every fulfilled line from the
  payload.
- But `ledger_tracking_unique (tracking_id)` allows only one ledger row per
  tracking row, and the insert is `ON CONFLICT DO NOTHING`. So **only the
  first line's fulfillment is recorded; the rest are silently dropped.**

**Evidence, last 30 days as of 2026-09-21:**

| Order type | Fulfilled preorder lines | With a −row |
|---|---|---|
| Single-line | 924 | **924** |
| Multi-line (148 orders) | 228 | **80** |

So **146 shipped units were never subtracted** in 30 days alone. Creations are
not affected: 664 of 664 multi-line create lines are recorded.

**Impact:**
- Open commitments are **overstated** for any title sold in multi-line orders.
- Lifecycle snapshots close only when commitment is ≤ 0, so affected titles
  may **never close**.
- Dashboard commitment figures are inflated.

**Unverified:** whether `orders/paid`, `orders/cancelled` and
`refunds/create` share the defect. They take the same single-row path in
`_extract_order_facts`. How far back the defect goes is also unknown.

**Needs:**
1. A dedicated investigation: the scope by topic and the full history.
2. A fix, either in the handler (one tracking row per line for every order
   topic) or in the builder (key idempotency on the per-topic natural keys
   that already exist, not on `tracking_id`).
3. A backfill of the missed rows from the stored payloads.
4. A decision on recomputing affected lifecycle snapshots.
5. Then complete R1's outstanding order, #80284.

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

### Landmine 11: shipping-profile system vs. market-driven shipping (hard deadline 2027-07-01)

Shopify is moving merchant shipping configuration out of delivery profiles and
into Markets ("market-driven shipping"). Source: the Shopify upgrade guide,
`shopify.dev/docs/apps/build/orders-fulfillment/market-driven-shipping/upgrade-your-app`,
read 2026-09-19. Shopify labels the dates as targets that may shift.

**Timeline:**
- **2026-10-01:** merchants can opt in. Shopify does not move a merchant
  automatically until it confirms the merchant's apps are compatible.
  **Manual opt-in before the app is upgraded causes breakage.**
- **2027-07-01:** market-driven shipping is on for **all** shops.

**Failure mode on a migrated shop:**
- Merchant-owned `deliveryProfile(s)` reads may return a stale snapshot.
- `deliveryProfileCreate`/`Update`/`Remove` writes on merchant profiles may
  **succeed with no error but change nothing**.
- App-owned delivery profiles are unaffected.
- Shopify's test: anything that targets a profile not created by your app is a
  merchant-profile interaction.

**Exposure of `services/shipping_profiles.py`:**
- It **reads** all profiles, including the merchant default.
- It **clones carrier config** from a reference profile
  (`SHIPPING_REFERENCE_PROFILE_GID`, likely merchant-owned).
- It **creates and repurposes** week profiles.
- Whether the week profiles count as app-owned in Shopify's model is **not yet
  verified**.

**Detection:** `shop { features { marketDrivenShipping } }`. The smoke script
reports it; this is informational and never blocks.

**Needs:**
1. **Now:** no one opts the shop into market-driven shipping.
2. **Plan a migration before 2027-07-01.** Shopify recommends an app-owned
   delivery profile ("app-profile integration"), which can use `coversAllItems`.
   Test it on a dev store with the feature preview.
3. **Submit Shopify's compatibility attestation** once migrated.

This is independent of the API version bump. It is triggered by shop state, not
by version.

### Landmine 12: `InventoryItem.variant` is deprecated (inventory-webhook path)

`shopify_service.INVENTORY_ITEM_TO_PRODUCT_QUERY` uses
`inventoryItem { variant { product { id } } }`. This query maps inventory
webhooks to products and feeds arrival records.

Shopify deprecated `InventoryItem.variant` in **2026-01** in favor of the
`InventoryItem.variants` connection. `variant` still works in all supported
versions, including `2026-07`, but will be removed in a future version.

**Ordering constraint:** the `variants` connection does **not** exist before
`2026-01`. The migration must ship only **after** production is confirmed
serving `2026-07`. Shipping it earlier would break inventory webhooks while
Railway still pins `2025-10`.

**Now unblocked (2026-09-21):** production serves `2026-07` (Landmine 3
resolved). The smoke run showed this deprecation header at `2026-07` only, as
predicted.

**Needs:** the migration PR. Rerun the smoke script before merge; the header
should disappear.

### Landmine 15: `Publication.name` is deprecated (`shopify_cleanup`)

`services/shopify_cleanup.py::PRODUCT_CLEANUP_STATE_QUERY` (operation
`ProductCleanupState`) requests `resourcePublicationsV2 { publication { id name } }`.

**Evidence:** the 2026-09-21 smoke run received an
`X-Shopify-API-Deprecated-Reason: Publication.name` header on **both** `2025-10`
and `2026-07`. So this predates the version bump; the 2026-01 → 2026-07
release-note review did not cover it. The field still works at `2026-07`.

**Risk: low.** The cleanup logic decides everything from the publication **ID**
(`CATCH_ALL_PUBLICATION_GID`). `name` is only copied into the returned
`publications` list for display.

**Needs:** migrate to the replacement documented by Shopify. Verify it in the
current docs at fix time; it is not assumed here. Then rerun
`scripts/smoke_shopify_api_version.py`; the deprecation header should
disappear.

### Landmine 16: other live repos share the 2026-10-16 API-version deadline

This is a separate org-wide sweep, not part of the pub-date phase. Status
below is owner-confirmed on 2026-09-19.

**Live**, so check each repo's Shopify usage and version setting:
- `webhook-gateway`
- `admin-dashboard`
- `supply-chain-service`
- `damaged-books-service` (code default `2025-10`)
- `sr-ops-suite` (code default `2025-10`; its `docs/SHOPIFY_API_VERSIONING.md`
  lists a worker and the admin-dashboard backend)
- `request-service` (code default `2024-10`)
- `door-list`
- `edelweiss-service`
- `backorder-service`

Code defaults matter only where the env var is unset. The org code search
matched the literal `admin/api`, so repos that build URLs differently were not
covered. The sweep needs its own inventory.

**Dead or dormant**, so ignore:
- `NYT_weekly_and_preorder_release`
- `preorder-slack-status`
- `setwise_inventory_manager`
- `shopify-reports`
- `used-books-automation`
- `ISBNFinder`
- `preorder-dashboard`
- `sandbox`
- `shopify-packingslip-enhancements`
- `events-directory`
- `kal-shipping`
- `weekly-nytimes-poaudit-reporting`
- `request-mgmt`
- `my-test-app`

**Deadline:** `2025-10` stops being accessible at **2026-10-16 15:00 UTC**.
For each live repo, confirm which version each deployed service actually
requests. preorder-service's own bump is done (see the resolved Landmine 3).

### Resolved

#### Landmine 3: pinned Shopify API version — RESOLVED 2026-09-21 (PR #27 + Railway change)

**Code: PR #27.**
- `shopify_version.get_api_version()` is the single source of truth, with a
  default of `2026-07`.
- A drift-guard test fails on any version literal outside it.
- `scripts/smoke_shopify_api_version.py` is a read-only, differential check.

**Smoke test.** Run by the owner on 2026-09-21 with production credentials,
baseline `2025-10`, target `2026-07`:
- **15 of 15** production read operations OK on both versions.
- 0 regressions, 0 pre-existing errors, 0 served-version mismatches.
- Deprecation headers:
  - `InventoryItem.variant` at `2026-07` only (Landmine 12).
  - `Publication.name` on both versions (Landmine 15).
- `marketDrivenShipping=False` (Landmine 11).
- Verdict: **SAFE TO BUMP**.

**Railway change.** `SHOPIFY_API_VERSION=2026-07` was set, and the service
redeployed at **2026-09-21 20:56:37 UTC**. The local `.env` was updated too.

**Post-deploy evidence:**
- The `order_tagger` job at 21:03 UTC called `admin/api/2026-07/graphql.json`:
  5 requests, all `200`, 0 errors.
- The `lifecycle_snapshotter` (21:01, 21:05) and `commitment_ledger` (21:05)
  jobs returned `ok: true`.
- The web service accepted and processed a webhook after the redeploy: an
  `orders/paid` delivered successfully, with its tracking row written.
- There have been no failed deliveries to preorder-service since 2026-09-11.

**Still open, split out:** the org-wide sweep of other repos is now
Landmine 16.

The original entry is preserved below.

##### (original) Landmine 3: pinned Shopify API version is about to become inaccessible

`2025-10` is accessible until **2026-10-16 15:00 UTC**. After that, Shopify
serves requests with the oldest accessible stable version. The latest stable
version is `2026-07`.

**Code side done (rev2 Move 0.2 PR):**
- All six call sites now go through `shopify_version.get_api_version()`. There
  had been three different defaults: `2025-10`, `2025-01`, and the legacy
  `API_VERSION` name.
- The code default is now `2026-07`.
- A test guards against drift.
- `scripts/smoke_shopify_api_version.py` provides a read-only differential check.

**Same deadline, other repos.** This is a separate org-wide sweep, not part of
the pub-date phase. Status below is owner-confirmed on 2026-09-19.

**Live**, so check each repo's Shopify usage and version setting:
- `webhook-gateway`
- `admin-dashboard`
- `supply-chain-service`
- `damaged-books-service` (code default `2025-10`)
- `sr-ops-suite` (code default `2025-10`; its `docs/SHOPIFY_API_VERSIONING.md`
  lists a worker and the admin-dashboard backend)
- `request-service` (code default `2024-10`)
- `door-list`
- `edelweiss-service`
- `backorder-service`

Code defaults matter only where the env var is unset. The org code search
matched the literal `admin/api`, so repos that build URLs differently were not
covered. The sweep needs its own inventory.

**Dead or dormant**, so ignore:
- `NYT_weekly_and_preorder_release`
- `preorder-slack-status`
- `setwise_inventory_manager`
- `shopify-reports`
- `used-books-automation`
- `ISBNFinder`
- `preorder-dashboard`
- `sandbox`
- `shopify-packingslip-enhancements`
- `events-directory`
- `kal-shipping`
- `weekly-nytimes-poaudit-reporting`
- `request-mgmt`
- `my-test-app`

**Still open — this is what actually moves production:** set
`SHOPIFY_API_VERSION=2026-07` on **every** Railway service that has it, after
the smoke script passes. The GitHub workflow's `API_VERSION` secret is covered
by Landmine 1. Resolve this landmine only once production is confirmed serving
`2026-07`.

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
