# intl-solo — International cart isolation

**Status:** PR 1 (read side) merged and its migration applied 2026-11-09. PR 2 (write side) in review. Owner-approved design, 2026-11-09.

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

Theme messaging (product page, add to cart, cart) is courtesy only. A Shopify Flow
workflow is the post-order backstop.

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
  grants, and `security_invoker` on the view. Applied 2026-11-09, except section 4
  (append-only log), which is pending.

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

**Drift:** the tag as of the last classification snapshot disagrees with the
decision (`tag_missing` / `tag_unexpected`). A stray tag on a non-preorder, such as
a hand-tagged test title, shows as `tag_unexpected`.

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

Auto-tagging new preorders after reclassification, and the scheduled drift sweep, arrive in
PR 3.

## Rollout order

1. Run the backfill as a dry run and review the list of titles it would tag.
2. Ship the theme messaging.
3. Apply the backfill. **International enforcement goes live at this step**,
   because the Cart Lock rule is already active on `intl-solo`.
4. Turn on the Flow backstop.
