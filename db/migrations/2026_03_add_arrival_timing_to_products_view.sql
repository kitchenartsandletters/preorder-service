-- db/migrations/2026_03_add_arrival_timing_to_products_view.sql
--
-- Adds arrival_timing from vw_arrival_timing into vw_preorder_products
-- and vw_preorder_release_queue so the frontend can read it from a
-- single endpoint rather than requiring a second fetch.

DROP VIEW IF EXISTS preorder.vw_preorder_metrics;
DROP VIEW IF EXISTS preorder.vw_preorder_release_candidates;
DROP VIEW IF EXISTS preorder.vw_preorder_release_queue;
DROP VIEW IF EXISTS preorder.vw_preorder_products;

CREATE VIEW preorder.vw_preorder_products AS
SELECT
    ps.product_id,
    ps.metadata_snapshot->>'title'  AS title,
    ps.metadata_snapshot->>'isbn'   AS isbn,
    GREATEST((ps.metadata_snapshot->>'inventory')::int, 0) AS inventory,

    COALESCE(live.presale_sales_total, 0) AS live_presale_qty,
    COALESCE(bf.backfill_presale_qty, 0)  AS estimated_presale_qty,
    COALESCE(live.presale_sales_total, 0)
        + COALESCE(bf.backfill_presale_qty, 0) AS total_presale_qty,

    CASE
        WHEN COALESCE(bf.backfill_presale_qty, 0) = 0 THEN 'verified'
        ELSE 'estimated'
    END AS data_confidence,

    ps.effective_pub_date AS pub_date,
    ps.status             AS classification,

    (ps.metadata_snapshot->>'preorder_tag_present')::boolean  AS preorder_tag_present,
    (ps.metadata_snapshot->>'in_preorder_collection')::boolean AS preorder_collection_present,

    CASE
        WHEN po.product_id IS NOT NULL THEN 'override'
        ELSE 'none'
    END AS override_status,

    ps.anomaly_type,

    -- arrival_timing joined directly so frontend needs one fetch
    at_view.arrival_timing,

    ps.last_classified_at AS last_updated

FROM preorder.product_status ps

LEFT JOIN (
    SELECT product_id,
        SUM(CASE
            WHEN topic IN ('orders/create', 'orders/paid')       THEN delta_qty
            WHEN topic IN ('orders/cancelled', 'refunds/create') THEN delta_qty
            ELSE 0
        END) AS presale_sales_total
    FROM preorder.commitment_ledger
    WHERE topic IN ('orders/create', 'orders/paid', 'orders/cancelled', 'refunds/create')
    GROUP BY product_id
) live ON live.product_id = ps.product_id

LEFT JOIN (
    SELECT product_id, SUM(delta_qty) AS backfill_presale_qty
    FROM preorder.commitment_ledger
    WHERE topic = 'orders/create_backfill'
    GROUP BY product_id
) bf ON bf.product_id = ps.product_id

LEFT JOIN preorder.product_overrides po ON po.product_id = ps.product_id

-- arrival_timing is a pure derived view — safe to join here
LEFT JOIN preorder.vw_arrival_timing at_view ON at_view.product_id = ps.product_id;


CREATE VIEW preorder.vw_preorder_release_queue AS
SELECT
    v.product_id,
    v.title,
    v.isbn,
    v.inventory,
    v.live_presale_qty,
    v.estimated_presale_qty,
    v.total_presale_qty,
    v.data_confidence,
    v.pub_date,
    v.classification,
    v.preorder_tag_present,
    v.preorder_collection_present,
    v.override_status,
    v.anomaly_type,
    v.arrival_timing,
    v.last_updated,

    CASE
        WHEN v.classification = 'active_preorder'
             AND v.pub_date IS NOT NULL
             AND v.pub_date <= CURRENT_DATE + INTERVAL '7 days'
        THEN true ELSE false
    END AS due_for_release_review,

    CASE
        WHEN v.inventory > 0
             AND v.classification = 'active_preorder'
             AND (v.pub_date IS NULL OR v.pub_date > CURRENT_DATE)
        THEN true ELSE false
    END AS early_stock_arrival

FROM preorder.vw_preorder_products v;


CREATE VIEW preorder.vw_preorder_metrics AS
SELECT
    COUNT(*) FILTER (WHERE classification = 'active_preorder')
        AS active_preorders,
    COUNT(*) FILTER (WHERE early_stock_arrival = true)
        AS early_arrivals,
    COUNT(*) FILTER (WHERE due_for_release_review = true)
        AS releases_due_for_review,
    SUM(live_presale_qty) FILTER (WHERE classification = 'active_preorder')
        AS total_live_presold_units,
    SUM(total_presale_qty) FILTER (WHERE classification = 'active_preorder')
        AS total_estimated_presold_units,
    COUNT(*) FILTER (
        WHERE classification = 'active_preorder'
          AND pub_date >= CURRENT_DATE
          AND pub_date < CURRENT_DATE + INTERVAL '7 days'
    ) AS releases_this_week
FROM preorder.vw_preorder_release_queue;