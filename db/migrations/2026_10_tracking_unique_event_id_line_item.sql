-- 2026_10_tracking_unique_event_id_line_item.sql
--
-- preorder.tracking is one row per LINE ITEM, but its unique constraint was on
-- event_id alone. That held only because preorder-service mints a fresh UUID
-- per row when X-Gateway-Event-ID arrives empty. When webhook-gateway began
-- sending a real, stable event id, every line after the first violated
-- tracking_event_id_key and the request 500'd — six failures 21:40-21:43 on
-- 2026-10-26, after which the gateway disabled the header (commit 0998d48).
--
-- NULLS NOT DISTINCT is required, not cosmetic: products/update and
-- inventory_levels/update store NULL line_item_id, and under default NULL
-- semantics two rows sharing an event_id with NULL line_item_id would BOTH be
-- allowed — silently removing dedupe from exactly the topics that are one row
-- per event.
--
-- Checked before applying (2026-10-27): 170,423 rows, 0 events with multiple
-- rows, 0 duplicate (event_id, line_item_id) pairs, 0 duplicate NULL-line
-- events. Across the 1,303 order events since 2026-10-24, including 411
-- multi-line, every line is present — no data was lost to the 500s, so there
-- is nothing to backfill.
--
-- The matching code change is ON CONFLICT (event_id, line_item_id) DO NOTHING
-- in routes/webhooks._insert_tracking: with stable event ids a gateway
-- REDELIVERY must be a no-op 200, not a 500 retried forever.

alter table preorder.tracking drop constraint tracking_event_id_key;

alter table preorder.tracking
  add constraint tracking_event_id_line_item_key
  unique nulls not distinct (event_id, line_item_id);
