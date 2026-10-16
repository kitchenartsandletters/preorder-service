-- 2026_10_add_hold_columns_to_release_queue.sql
--
-- Expose the hold columns on preorder.vw_preorder_release_queue.
--
-- 2026_10_create_preorder_hold.sql added hold_until / hold_reason / hold_note /
-- hold_active to vw_preorder_products, but the release-queue view selects its
-- columns one by one from that view rather than SELECT *, so the hold never
-- reached /upcoming. The dashboard worked around it by copying the fields
-- across from /products by product_id; with these columns the view's own
-- values take over and that join becomes redundant.
--
-- Additive: the four columns are appended, so every existing column keeps its
-- name, type and position.

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
    override_status,
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
