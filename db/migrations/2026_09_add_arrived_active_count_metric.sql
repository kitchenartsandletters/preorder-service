-- 2026_09_add_arrived_active_count_metric.sql
--
-- Adds arrived_active_count to vw_preorder_metrics.
--
-- Context: a preorder whose stock physically arrived (a live inventory_arrival
-- record, created within 60s of first_positive_inventory_at) but whose demand
-- has since oversold it into negative inventory stays classified active_preorder
-- (correct: future pub date, live demand, no stock on hand). The classifier's
-- early_stock_arrival path requires inventory >= 0, so these do not surface as
-- early arrivals. Result: the system was blind to "stock received but still
-- active", which matters because such titles are fulfillable and should be
-- detached from their date/week shipping profile early.
--
-- This metric makes that bucket countable for the dashboard. arrival is a
-- permanent fact (arrival_record_is_live), independent of current inventory
-- sign, so the count keys off that flag rather than inventory.
--
-- Classification is intentionally NOT changed — active_preorder remains
-- semantically honest. The shipping-profile detach action keyed off the same
-- arrival signal is handled separately in the week-reconcile layer.
--
-- Additive column via CREATE OR REPLACE (no cascade): vw_preorder_metrics is a
-- leaf view; appending a trailing column does not require dropping the
-- products/release_queue views it reads from.

CREATE OR REPLACE VIEW preorder.vw_preorder_metrics AS
SELECT count(*) FILTER (WHERE classification = 'active_preorder'::text) AS active_preorders,
    count(*) FILTER (WHERE classification = 'early_stock_arrival'::text OR classification = 'active_preorder'::text AND inventory > 0) AS early_arrivals,
    count(*) FILTER (WHERE due_for_release_review = true) AS releases_due_for_review,
    sum(live_presale_qty) FILTER (WHERE classification = ANY (ARRAY['active_preorder'::text, 'early_stock_arrival'::text])) AS total_live_presold_units,
    sum(total_presale_qty) FILTER (WHERE classification = ANY (ARRAY['active_preorder'::text, 'early_stock_arrival'::text])) AS total_estimated_presold_units,
    count(*) FILTER (WHERE pub_date >= (CURRENT_DATE - EXTRACT(dow FROM CURRENT_DATE)::integer::double precision * '1 day'::interval)::date AND pub_date <= (CURRENT_DATE - EXTRACT(dow FROM CURRENT_DATE)::integer::double precision * '1 day'::interval + '6 days'::interval)::date AND (classification = ANY (ARRAY['active_preorder'::text, 'early_stock_arrival'::text, 'historical_preorder'::text]))) AS releases_this_week,
    ( SELECT count(*) AS count
           FROM preorder.product_status ps2
             JOIN preorder.inventory_arrival ia2 ON ia2.product_id = ps2.product_id
             JOIN preorder.vw_arrival_timing at2 ON at2.product_id = ps2.product_id
             LEFT JOIN preorder.lifecycle_snapshot ls2 ON ls2.product_id = ps2.product_id
          WHERE ps2.status = 'historical_preorder'::text AND at2.arrival_timing = 'late_arrival'::text AND EXTRACT(epoch FROM ia2.created_at - ia2.first_positive_inventory_at) < 60::numeric AND ls2.lifecycle_closed_at IS NULL AND ia2.first_positive_inventory_at <= ((ps2.effective_pub_date::timestamp without time zone AT TIME ZONE 'America/New_York'::text) + '90 days'::interval)) AS late_arrivals_unresolved,
    ( SELECT count(*) AS count
           FROM preorder.product_status ps3
             LEFT JOIN preorder.inventory_arrival ia3 ON ia3.product_id = ps3.product_id
             JOIN preorder.vw_arrival_timing at3 ON at3.product_id = ps3.product_id
          WHERE ps3.status = 'historical_preorder'::text AND at3.arrival_timing = 'no_arrival'::text AND ps3.effective_pub_date IS NOT NULL AND ps3.effective_pub_date < CURRENT_DATE AND ps3.effective_pub_date >= '2026-02-11'::date AND NOT (ps3.product_id IN ( SELECT alert_dismissals.product_id
                   FROM preorder.alert_dismissals
                  WHERE alert_dismissals.alert_type = 'no_arrival'::text))) AS no_arrival_count,
    count(*) FILTER (WHERE classification = 'active_preorder'::text AND arrival_record_is_live = true) AS arrived_active_count
   FROM preorder.vw_preorder_release_queue;
