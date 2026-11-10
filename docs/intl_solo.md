# intl-solo — International cart isolation

**Status:** Complete and live since 2026-11-10. All layers are in place: theme messaging,
Cart Lock enforcement, dashboard controls, automation, and the Shopify Flow backstop.
Owner-approved design, 2026-11-09. Built in preorder-service #52, #53, #54, #55 and
admin-dashboard #114 plus its modal-close fix.

## Why

Market (international) orders ship whole: splitting one after it is placed voids
the duties and taxes paid at checkout. A title that cannot ship yet therefore has
to travel alone. Domestic orders don't need this, because shipping profiles price
and ship preorders by release week.

## Enforcement

The Shopify product tag `intl-solo` is enforced at checkout by the **Cart Lock**
app (Shopify Functions validation, works on the Advanced plan). The rule blocks an
order when all three are true:

1. Unique product count is more than 1 (the variant/tag filter is **off**, so
   every product in the cart counts)
2. Shipping country is not the United States (it reads the shipping address, not
   the Markets country)
3. A product tagged `intl-solo` is in the cart

The rule's advanced setting "Check rules after customer details are entered" is
**on**. Without it, an empty address reads as non-US and blocks.

Tested 2026-11-09: a single title and multiple copies are allowed; a title plus
another book, two different tagged titles, and an address changed from US to UK
are all blocked; express checkout is blocked. US orders are allowed.

## The other layers

Cart Lock is the enforcement. The rest either warns customers earlier or catches what
gets through.

- **Theme (courtesy only).** `assets/custom.js` checks the cart before an international
  add-to-cart and blocks a mix, naming the titles in a modal. The cart drawer and cart page
  show a notice and disable checkout. Product pages show a one-line note to international
  visitors, from `localization.country` in `main-product.liquid`. Products expose
  `intlSolo` and `title` in their `product-variant-data` JSON (`main-product.liquid`,
  `product-grid-item.liquid`). Every failure fails open, because Cart Lock is the authority.
  This replaced the old hardcoded Noma / On Eating logic.
- **Shopify Flow (post-order backstop).** It catches an order that got past Cart Lock.
  - *Detect intl solo preorder violations*: on Order created, Run code flags a shipping
    country other than the US, an `intl-solo` product, and more than one distinct product.
    It tags the order `intl-solo-violation`, holds any fulfillment orders that already
    exist, and emails letters@kitchenartsandletters.com.
  - *Hold intl solo violation fulfillments*: on Fulfillment order ready to fulfill, it
    holds anything tagged `intl-solo-violation`. This covers fulfillment orders created
    after the first workflow ran.

  Market orders can't be split without voiding prepaid duties, so a flagged order is
  **cancelled in full** and the customer is asked to reorder separately.

## Lifecycle (independent of the domestic preorder lifecycle)

- **Restricted** automatically when a title becomes a preorder (`active_preorder`,
  `early_stock_arrival`, or in the Preorder collection).
- **Stays restricted** through the pub date, Full Cleanup, holds, and any domestic
  status change. **Full Cleanup never removes `intl-solo`.**
- **Cleared only manually** in the dashboard, with a required reason:
  - `received`: stock has physically arrived (an `inventory_arrival` row exists,
    whatever current inventory says)
  - `guaranteed`: stock is guaranteed before it arrives
- **Early arrivals** (stock before the pub date) are reviewed case by case.
- `force_on` restricts a title that isn't a preorder.

## Data

- `preorder.intl_solo_state`: the decision of record (state, basis, reason, who)
- `preorder.intl_solo_log`: append-only audit trail
- `preorder.vw_intl_solo_inputs`: raw inputs; state is derived in
  `services/intl_solo_service.py` (pure, tested)

Migrations:
- `db/migrations/2026_11_create_intl_solo_state.sql`: tables and view. Applied 2026-11-09.
  It doesn't touch `vw_preorder_products`, so it is independent of the pending Move 1g
  view rewrite.
- `db/migrations/2026_11_intl_solo_rls.sql`: RLS on with no policies, `service_role`-only
  grants, and `security_invoker` on the view. Applied 2026-11-09, including section 4
  (append-only log).
- `db/migrations/2026_11_intl_solo_tag_confirmation.sql`: `tag_confirmed` /
  `tag_confirmed_at` on the decision row, plus the view. Applied 2026-11-10.

## Display states

| State | Meaning | Actions |
|---|---|---|
| `restricted_past_pub` | Pub date reached, no stock. The hard-enforcement case | clear_guaranteed |
| `early_arrival_review` | Stock in before the pub date (or no pub date) | clear_received, clear_guaranteed |
| `ready_to_clear` | Stock arrived on or after the pub date | clear_received |
| `restricted` | Restricted, no stock, pub date ahead | clear_guaranteed |
| `pending_restriction` | A preorder with no decision yet. Auto-tag or backfill will restrict it | — |
| `cleared` | Cleared, with basis | restore |
| `not_applicable` | Not a preorder, no decision | force_on |

**Drift:** the Shopify tag disagrees with the decision (`tag_missing` /
`tag_unexpected`). A stray tag on a non-preorder, such as a hand-tagged test title,
shows as `tag_unexpected`.

The tag comes from whichever is newer: the classification snapshot (refreshed when
Shopify's `products/update` webhook is processed), or the last tag write Shopify
accepted from this module (`tag_confirmed`, `tag_confirmed_at`). Without the second
source, every action showed a few seconds of false drift until the webhook caught
up. A snapshot taken after the write still wins, so a change made in Shopify admin
shows as drift as before. Each derived row reports `tag_source` (`snapshot` or
`confirmed_write`).

## Endpoints (under `/admin/preorders`)

- `GET /intl-solo`: derived state per product plus summary counts
  (`?include_not_applicable=true` lists every product)
- `GET /intl-solo/log`: audit log (`?product_id=`, `?limit=`)
- `POST /intl-solo/{product_id}/clear`: body `{basis: "received" | "guaranteed", reason, changed_by}`
- `POST /intl-solo/{product_id}/restore`: body `{reason, changed_by}`. Undoes a clearance
- `POST /intl-solo/{product_id}/force-on`: body `{reason, changed_by}`. Restricts a non-preorder
- `POST /intl-solo/reconcile`: body `{apply, confirm, product_ids, changed_by}`. A dry run by
  default. Applying needs `confirm: "apply"`. The plan restricts pending titles (this is the
  backfill), re-adds missing tags, and removes tags from cleared titles. A stray tag with no
  decision behind it (such as a test title) is reported, never removed.

Errors: 400 bad input, 404 unknown product, 409 action not allowed in the current state,
502 Shopify refused a tag write.

Every action is validated against the same `ALLOWED_ACTIONS` table the dashboard renders
from, and every action is logged.

### Write order: fail toward restricting

The decision and the Shopify tag can't be written atomically, so each action orders them so
that a failure part-way leaves the title **restricted**:

- **Restricting** (restore, force_on, reconcile): add the tag, then record the decision. If
  Shopify refuses, nothing is recorded.
- **Clearing**: record the decision, then remove the tag. If Shopify refuses, the title stays
  restricted at checkout and shows as drift until reconcile retries.

Tag writes use `tagsAdd`/`tagsRemove`, never `productUpdate`, which replaces the whole tag
list. Both are idempotent.

## Automation

**Automation only ever restricts.** It restricts new preorders and re-adds a missing tag.
It never clears a title or removes a tag; lifting a restriction is always a person's
decision. A bug here can cost a sale, never let a mixed international order through.

- **After reclassification.** `services/reclassification_service.reclassify_single_product`
  calls `intl_solo_actions.auto_restrict` once the product is classified. That covers the
  `products/update` and inventory webhooks, the pub-date transition job and the pub-date
  admin route. A preorder with no decision is restricted (`basis: auto_preorder`,
  `changed_by: auto`); a restricted title missing its tag gets it back. It never raises, so
  it can't break the webhook. The tag write fires another `products/update`, whose
  reclassification finds the title restricted and tagged and does nothing.
- **Scheduled sweep.** `python jobs/run.py --job intl_solo_sweep` (add `--dry-run` to
  preview) catches anything a missed webhook left behind. It applies only `restrict` and
  `tag_add`. Tag removals and stray tags are listed under `needs_person` in its JSON
  summary, for someone to handle in the dashboard.
- **Kill switch.** `INTL_SOLO_AUTO_TAG=off` stops both. The dashboard and the manual
  reconcile endpoint keep working.

## Operations

- **Daily use** is the dashboard: Preorders > Release Management. Restricted titles show
  🌐 Intl solo, and the *International — Decision Due* panel lists titles past their pub
  date without stock, early arrivals, titles ready to clear, and any tag drift. *Intl…* on a
  row clears or restores a title, with a required reason.
- **Sweep** runs hourly as the Railway cron service `intl-solo-sweep`
  (`python jobs/run.py --job intl_solo_sweep`). It needs the same variables as the other
  cron services, because `jobs/run.py` imports every job at startup. Its JSON summary lists
  anything that needs a person under `needs_person`.
- **Pausing automation:** set `INTL_SOLO_AUTO_TAG=off` on the web service (webhook
  auto-tagging) and on `intl-solo-sweep`. Restrictions already in place stay enforced.
- **Bulk product edits:** `auto_restrict` runs inside every reclassification and can add a
  tag write. Set `INTL_SOLO_AUTO_TAG=off` on the web service for the duration of any bulk
  edit (see DOCS_STATUS Amendment A1 / gate G5), then turn it back on and let the hourly
  sweep catch up. The sweep also covers lost webhooks, per Amendment A2.
- **Turning enforcement off entirely:** disable the Cart Lock rule. Tags and decisions stay
  as they are, so turning it back on resumes where it left off.
- **Manual reconcile** (dry run first):
  `POST /admin/preorders/intl-solo/reconcile` with `{}`, then
  `{"apply": true, "confirm": "apply", "changed_by": "<name>"}`.

## Rollout (completed 2026-11-10)

1. Dry-run backfill reviewed: 13 titles.
2. Theme messaging published.
3. Backfill applied, which is when international enforcement went live. 13 restricted, 0
   failures, 0 drift.
4. Dashboard controls shipped (admin-dashboard #114).
5. Automation shipped (#55). The `intl-solo-sweep` cron service is scheduled and has run
   cleanly.
6. Shopify Flow backstop set up (the two workflows above).

## Possible follow-ups

- If cancellations from Flow turn out to be frequent, look at where orders get past Cart
  Lock before adding more automation.
- Phase 1 isolates every preorder title individually. Grouping compatible titles (for
  example, the same release week) was deliberately left out, and would need a policy
  decision first.
