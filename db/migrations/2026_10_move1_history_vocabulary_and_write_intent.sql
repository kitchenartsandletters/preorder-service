-- 2026_10_move1_history_vocabulary_and_write_intent.sql
-- rev2 Move 1a. Applied to production 2026-10-27.
--
-- Extends the EXISTING preorder.pubdate_history (decision D2: no second table)
-- and adds the write-intent table the sanctioned pub-date editor needs (3.5).

-- 1. change_source vocabulary. Includes the historical values so existing rows
--    stay valid: initial_baseline (1642), shopify_pub_date (283, frozen by 1b),
--    override_date (56, frozen by 1e), legacy_tag_fallback (0, never written,
--    kept only so the constraint admits the historical vocabulary).
alter table preorder.pubdate_history
  add constraint pubdate_history_change_source_check
  check (change_source in (
    'initial_baseline',
    'shopify_pub_date',
    'override_date',
    'legacy_tag_fallback',
    'ui_change',
    'shopify_direct_edit',
    'override_migration',
    'tag_history_backfill'
  ));

-- 2. The history panel reads one product newest-first; the existing indexes are
--    on product_id and changed_at separately.
create index if not exists pubdate_history_product_changed_idx
  on preorder.pubdate_history (product_id, changed_at desc);

-- 3. Write intents. The dashboard records an intent immediately before calling
--    metafieldsSet, so the webhook Shopify sends back for that same edit is
--    labelled ui_change rather than shopify_direct_edit. Whichever path commits
--    first, the other finds no diff and writes nothing.
create table if not exists preorder.pubdate_write_intent (
    id          uuid        primary key default gen_random_uuid(),
    product_id  bigint      not null,
    new_date    date        not null,
    changed_by  text,
    reason      text,
    created_at  timestamptz not null default now(),
    consumed_at timestamptz
);

comment on table preorder.pubdate_write_intent is
  'Short-lived record of a sanctioned pub-date edit, so the resulting webhook is labelled ui_change rather than shopify_direct_edit. See docs/phase_unified_pubdate_rev2.md 3.5.';

create index if not exists pubdate_write_intent_open_idx
  on preorder.pubdate_write_intent (product_id, new_date)
  where consumed_at is null;
