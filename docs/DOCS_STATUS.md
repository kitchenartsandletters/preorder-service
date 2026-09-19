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
| `docs/phase_unified_pubdate_and_tag_simplification.md` | **DRAFT PROPOSAL — approved in principle, not yet implemented.** Plan to collapse the date model to a single `pub_date` SoT + `pub_date_history` table, retire the `override_date` metafield, delete `date_tags` (after mining into history), and demote the `preorder` tag. Its §2 current-state citations were verified at authoring (2026-09-19) but the doc itself instructs re-verifying against live code before building. Describes FUTURE state — do NOT read it as how the system works today. |
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
