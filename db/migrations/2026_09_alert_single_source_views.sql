-- 2026_09_alert_single_source_views.sql
--
-- Single source of truth for the late-arrival and no-arrival alerts.
--
-- Background: the alert COUNT (vw_preorder_metrics) and the alert LIST (the
-- /late-arrivals and /no-arrival-titles endpoints) were computed by separate
-- code paths. They drifted three times:
--   1. regen week-bounds mismatch,
--   2. a Python naive/aware datetime compare that threw so the list returned
--      empty while the count kept counting,
--   3. the late-arrivals count missing the alert_dismissals exclusion that the
--      list (and the no-arrival count) had — so dismissed titles kept being
--      counted, leaving a ghost count with an empty expand panel.
--
-- Fix: encode each alert's full definition in ONE view. The metric counts the
-- view; the endpoint selects the view. Same query, cannot disagree.
--
-- To change an alert's logic, change the view here — never re-derive it in the
-- endpoint or inline in the metric.

-- ── Late arrivals ─────────────────────────────────────────────────────────────
-- historical_preorder + late_arrival timing + live arrival (<60s delta between
-- record creation and first positive inventory) + open lifecycle + arrived
-- within 90 days of pub date + not dismissed.
CREATE OR REPLACE VIEW preorder.vw_late_arrivals_unresolved AS
SELECT
    ps.product_id,
    ps.metadata_snapshot->>'title' AS title,
    ps.metadata_snapshot->>'isbn'  AS isbn,
    ps.effective_pub_date          AS pub_date,
    at.arrival_timing,
    ia.first_positive_inventory_at,
    ia.created_at                  AS arrival_recorded_at
FROM preorder.product_status ps
JOIN preorder.inventory_arrival ia ON ia.product_id = ps.product_id
JOIN preorder.vw_arrival_timing at ON at.product_id = ps.product_id
LEFT JOIN preorder.lifecycle_snapshot ls ON ls.product_id = ps.product_id
WHERE ps.status = 'historical_preorder'
  AND at.arrival_timing = 'late_arrival'
  AND EXTRACT(EPOCH FROM (ia.created_at - ia.first_positive_inventory_at)) < 60
  AND ls.lifecycle_closed_at IS NULL
  AND ia.first_positive_inventory_at
      <= ((ps.effective_pub_date::timestamp AT TIME ZONE 'America/New_York') + INTERVAL '90 days')
  AND ps.product_id NOT IN (
        SELECT product_id FROM preorder.alert_dismissals
        WHERE alert_type = 'late_arrival'
  );

-- ── No arrivals ───────────────────────────────────────────────────────────────
-- historical_preorder + no_arrival timing + past pub date + post-cutover
-- (>= 2026-02-11, before which inventory wasn't tracked) + not dismissed.
CREATE OR REPLACE VIEW preorder.vw_no_arrival_unresolved AS
SELECT
    ps.product_id,
    ps.metadata_snapshot->>'title' AS title,
    ps.metadata_snapshot->>'isbn'  AS isbn,
    ps.effective_pub_date          AS pub_date,
    at.arrival_timing,
    ps.status                       AS classification
FROM preorder.product_status ps
LEFT JOIN preorder.inventory_arrival ia ON ia.product_id = ps.product_id
JOIN preorder.vw_arrival_timing at ON at.product_id = ps.product_id
WHERE ps.status = 'historical_preorder'
  AND at.arrival_timing = 'no_arrival'
  AND ps.effective_pub_date IS NOT NULL
  AND ps.effective_pub_date < CURRENT_DATE
  AND ps.effective_pub_date >= '2026-02-11'
  AND ps.product_id NOT IN (
        SELECT product_id FROM preorder.alert_dismissals
        WHERE alert_type = 'no_arrival'
  );

-- ── Metrics view now counts the source-of-truth views ─────────────────────────
-- late_arrivals_unresolved and no_arrival_count are simple counts of the views
-- above, instead of re-deriving the filters inline (which is how the dismissal
-- guard got dropped from the late-arrivals count).
CREATE OR REPLACE VIEW preorder.vw_preorder_metrics AS
SELECT count(*) FILTER (WHERE classification = 'active_preorder'::text) AS active_preorders,
    count(*) FILTER (WHERE classification = 'early_stock_arrival'::text OR classification = 'active_preorder'::text AND inventory > 0) AS early_arrivals,
    count(*) FILTER (WHERE due_for_release_review = true) AS releases_due_for_review,
    sum(live_presale_qty) FILTER (WHERE classification = ANY (ARRAY['active_preorder'::text, 'early_stock_arrival'::text])) AS total_live_presold_units,
    sum(total_presale_qty) FILTER (WHERE classification = ANY (ARRAY['active_preorder'::text, 'early_stock_arrival'::text])) AS total_estimated_presold_units,
    count(*) FILTER (WHERE pub_date >= (CURRENT_DATE - EXTRACT(dow FROM CURRENT_DATE)::integer::double precision * '1 day'::interval)::date AND pub_date <= (CURRENT_DATE - EXTRACT(dow FROM CURRENT_DATE)::integer::double precision * '1 day'::interval + '6 days'::interval)::date AND (classification = ANY (ARRAY['active_preorder'::text, 'early_stock_arrival'::text, 'historical_preorder'::text]))) AS releases_this_week,
    ( SELECT count(*) FROM preorder.vw_late_arrivals_unresolved ) AS late_arrivals_unresolved,
    ( SELECT count(*) FROM preorder.vw_no_arrival_unresolved ) AS no_arrival_count,
    count(*) FILTER (WHERE classification = 'active_preorder'::text AND arrival_record_is_live = true) AS arrived_active_count
   FROM preorder.vw_preorder_release_queue;
