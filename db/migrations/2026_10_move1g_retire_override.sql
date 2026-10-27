-- 2026_10_move1g_retire_override.sql
-- rev2 Move 1g. NOT YET APPLIED — see the sequencing note below.
--
-- Retires the preorder override from the database. The code side (Move 1e,
-- deployed 2026-10-27) already stopped reading it, and the values were folded
-- into pub_date on 2026-10-27 (Move 1d, 37 products, recorded in
-- preorder.pubdate_history as override_migration).
--
-- SEQUENCING — APPLY ONLY AFTER THE DASHBOARD CHANGE IS MERGED.
-- vw_preorder_products.override_status is rendered by admin-dashboard's
-- PreorderDetailSidebar and mapped in its API layer. Dropping the column before
-- their removal ships would make the sidebar select a column that no longer
-- exists. Coordinate, then apply.
--
-- preorder.product_overrides holds exactly one row (product 12345, test junk)
-- and has no writer: update_override_date_and_reclassify was never routed and
-- would have failed anyway, writing an updated_by column the table lacks
-- (docs/DOCS_STATUS.md Landmine 6).

-- 1. Drop override_status from the products view. Every other column keeps its
--    name, type and position; the hold columns stay at the end.
create or replace view preorder.vw_preorder_products as
 SELECT ps.product_id,
    ps.metadata_snapshot ->> 'title'::text AS title,
    ps.metadata_snapshot ->> 'isbn'::text AS isbn,
    GREATEST((ps.metadata_snapshot ->> 'inventory'::text)::integer, 0) AS inventory,
    COALESCE(live.presale_sales_total, 0::bigint) AS live_presale_qty,
    COALESCE(bf.backfill_presale_qty, 0::bigint) AS estimated_presale_qty,
    COALESCE(live.presale_sales_total, 0::bigint) + COALESCE(bf.backfill_presale_qty, 0::bigint) AS total_presale_qty,
        CASE
            WHEN COALESCE(bf.backfill_presale_qty, 0::bigint) = 0 THEN 'verified'::text
            ELSE 'estimated'::text
        END AS data_confidence,
    ps.effective_pub_date AS pub_date,
    ps.status AS classification,
    (( SELECT true
           FROM jsonb_array_elements_text(
                CASE
                    WHEN jsonb_typeof(ps.metadata_snapshot -> 'tags'::text) = 'array'::text THEN ps.metadata_snapshot -> 'tags'::text
                    ELSE '[]'::jsonb
                END) t(tag)
          WHERE t.tag = 'preorder'::text
         LIMIT 1)) IS NOT NULL AS preorder_tag_present,
    (ps.metadata_snapshot ->> 'in_preorder_collection'::text)::boolean AS preorder_collection_present,
    ps.anomaly_type,
    at_view.arrival_timing,
    ia.first_positive_inventory_at,
    ls.lifecycle_closed_at IS NOT NULL AS lifecycle_closed,
        CASE
            WHEN ia.product_id IS NULL THEN false
            WHEN EXTRACT(epoch FROM ia.created_at - ia.first_positive_inventory_at) < 60::numeric THEN true
            ELSE false
        END AS arrival_record_is_live,
        CASE
            WHEN rs.product_id IS NOT NULL THEN true
            ELSE false
        END AS already_reported,
    ps.last_classified_at AS last_updated,
    h.hold_until,
    h.reason AS hold_reason,
    h.note AS hold_note,
    (h.product_id IS NOT NULL AND h.hold_until >= (now() AT TIME ZONE 'America/New_York'::text)::date) AS hold_active
   FROM preorder.product_status ps
     LEFT JOIN ( SELECT cl.product_id,
            sum(
                CASE
                    WHEN cl.topic = ANY (ARRAY['orders/create'::text, 'orders/paid'::text]) THEN cl.delta_qty
                    WHEN cl.topic = ANY (ARRAY['orders/cancelled'::text, 'refunds/create'::text]) THEN cl.delta_qty
                    ELSE 0
                END) AS presale_sales_total
           FROM preorder.commitment_ledger cl
             JOIN preorder.product_status ps2 ON ps2.product_id = cl.product_id
          WHERE (cl.topic = ANY (ARRAY['orders/create'::text, 'orders/paid'::text, 'orders/cancelled'::text, 'refunds/create'::text])) AND (ps2.status <> 'historical_preorder'::text OR ps2.effective_pub_date IS NOT NULL AND cl.occurred_at < (ps2.effective_pub_date::timestamp without time zone AT TIME ZONE 'America/New_York'::text))
          GROUP BY cl.product_id) live ON live.product_id = ps.product_id
     LEFT JOIN ( SELECT commitment_ledger.product_id,
            sum(commitment_ledger.delta_qty) AS backfill_presale_qty
           FROM preorder.commitment_ledger
          WHERE commitment_ledger.topic = 'orders/create_backfill'::text
          GROUP BY commitment_ledger.product_id) bf ON bf.product_id = ps.product_id
     LEFT JOIN preorder.vw_arrival_timing at_view ON at_view.product_id = ps.product_id
     LEFT JOIN preorder.inventory_arrival ia ON ia.product_id = ps.product_id
     LEFT JOIN preorder.lifecycle_snapshot ls ON ls.product_id = ps.product_id
     LEFT JOIN preorder.release_state rs ON rs.product_id = ps.product_id AND rs.released_to_reporting = true
     LEFT JOIN preorder.preorder_hold h ON h.product_id = ps.product_id;

-- 2. The release-queue view selects its columns one by one from the products
--    view, so it carries override_status too.
create or replace view preorder.vw_preorder_release_queue as
 SELECT product_id,
    title,
    isbn,
    inventory,
    live_presale_qty,
    estimated_presale_qty,
    total_presale_qty,
    data_confidence,
    pub_date,
    classification,
    preorder_tag_present,
    preorder_collection_present,
    anomaly_type,
    arrival_timing,
    first_positive_inventory_at,
    lifecycle_closed,
    arrival_record_is_live,
    already_reported,
    last_updated,
        CASE
            WHEN (classification = ANY (ARRAY['active_preorder'::text, 'early_stock_arrival'::text])) AND pub_date IS NOT NULL AND pub_date <= (CURRENT_DATE + '7 days'::interval) AND pub_date >= CURRENT_DATE THEN true
            ELSE false
        END AS due_for_release_review,
        CASE
            WHEN classification = 'early_stock_arrival'::text THEN true
            WHEN inventory > 0 AND classification = 'active_preorder'::text AND pub_date IS NOT NULL AND pub_date > CURRENT_DATE THEN true
            ELSE false
        END AS early_stock_arrival,
    hold_until,
    hold_reason,
    hold_note,
    hold_active
   FROM preorder.vw_preorder_products v;

-- 3. Drop the table. Nothing reads it once the views above are replaced.
drop table if exists preorder.product_overrides;
