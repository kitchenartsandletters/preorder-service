-- db/migrations/2026_03_create_vw_live_presale_metrics.sql
--
-- vw_live_presale_metrics
--
-- Answers the six original business questions using only verified
-- Tier 1 event data (occurred_at >= 2026-02-11, live topics only).
--
-- See docs/Trust_Tier_Labeling.md for tier definitions and permitted claims.
--
-- This view is the ONLY source permitted for:
--   - active preorder monitoring
--   - open obligation figures surfaced to operations
--   - any figure labeled confidence: verified
--
-- It does NOT cover historical titles active before 2026-02-11.
-- For those, use estimated_presale_qty from vw_preorder_products with
-- data_confidence = 'estimated' clearly labeled in the output.

CREATE OR REPLACE VIEW preorder.vw_live_presale_metrics AS
WITH

-- All live positive presale events (creates + paid), pre-pub-date boundary only.
-- This is presale_sales_total per the Trust Tier document.
presale_cohort AS (
    SELECT
        cl.product_id,
        COUNT(DISTINCT cl.order_id)             AS presale_order_count,
        SUM(cl.delta_qty)                       AS presale_sales_total
    FROM preorder.commitment_ledger cl
    JOIN preorder.product_status ps ON ps.product_id = cl.product_id
    WHERE cl.topic IN ('orders/create', 'orders/paid')
      AND cl.occurred_at >= '2026-02-11'::timestamptz
      AND cl.occurred_at < (ps.effective_pub_date::timestamptz AT TIME ZONE 'America/New_York')
    GROUP BY cl.product_id
),

-- Pre-pub cancellations and refunds that reduce the presale cohort.
presale_reductions AS (
    SELECT
        cl.product_id,
        SUM(cl.delta_qty) AS reduction_qty
    FROM preorder.commitment_ledger cl
    JOIN preorder.product_status ps ON ps.product_id = cl.product_id
    WHERE cl.topic IN ('orders/cancelled', 'refunds/create')
      AND cl.occurred_at >= '2026-02-11'::timestamptz
      AND cl.occurred_at < (ps.effective_pub_date::timestamptz AT TIME ZONE 'America/New_York')
    GROUP BY cl.product_id
),

-- Fulfillments against the presale cohort.
-- Used to derive open_presale_commitments — not presale_sales_total.
presale_fulfillments AS (
    SELECT
        cl.product_id,
        SUM(cl.delta_qty) AS fulfilled_qty
    FROM preorder.commitment_ledger cl
    JOIN preorder.product_status ps ON ps.product_id = cl.product_id
    WHERE cl.topic = 'orders/fulfilled'
      AND cl.occurred_at >= '2026-02-11'::timestamptz
    GROUP BY cl.product_id
)

SELECT
    ps.product_id,
    ps.metadata_snapshot->>'title'              AS title,
    ps.metadata_snapshot->>'isbn'               AS isbn,
    ps.status                                   AS classification,
    ps.effective_pub_date,
    ps.anomaly_type,

    -- Business question 1: Is this an active preorder?
    -- Answered by classification column above.

    -- Business question 2: How many presales did we take?
    -- presale_sales_total = creates + paid - cancellations - refunds, pre-pub only.
    COALESCE(pc.presale_sales_total, 0)
        + COALESCE(pr.reduction_qty, 0)         AS presale_sales_total,

    COALESCE(pc.presale_order_count, 0)         AS presale_order_count,

    -- Business question 3 + 4: Have commitments been fulfilled?
    -- open_presale_commitments = presale_sales_total - fulfillments to date.
    -- When this reaches 0 and inventory has arrived, lifecycle can close.
    COALESCE(pc.presale_sales_total, 0)
        + COALESCE(pr.reduction_qty, 0)
        + COALESCE(pf.fulfilled_qty, 0)         AS open_presale_commitments,

    COALESCE(pf.fulfilled_qty, 0)               AS fulfilled_qty,

    -- Business question 5: Can this title be sold normally?
    -- post_pub_date = pub date has passed
    -- lifecycle_closed = snapshot exists and is closed
    CASE WHEN ps.effective_pub_date < CURRENT_DATE THEN true ELSE false END
                                                AS post_pub_date,
    CASE WHEN ls.lifecycle_closed_at IS NOT NULL THEN true ELSE false END
                                                AS lifecycle_closed,

    -- Business question 6: Anomalies
    -- Answered by anomaly_type column above.

    -- Inventory context
    ia.first_positive_inventory_at,
    GREATEST((ps.metadata_snapshot->>'inventory')::int, 0)
                                                AS current_inventory,

    -- Confidence label — always 'verified' in this view by construction.
    -- This view only contains post-cutover live event data.
    'verified'::text                            AS data_confidence,

    -- Audit trail
    '2026-02-11'::date                          AS cutover_date,
    ps.last_classified_at

FROM preorder.product_status ps

LEFT JOIN presale_cohort     pc ON pc.product_id = ps.product_id
LEFT JOIN presale_reductions pr ON pr.product_id = ps.product_id
LEFT JOIN presale_fulfillments pf ON pf.product_id = ps.product_id
LEFT JOIN preorder.lifecycle_snapshot ls ON ls.product_id = ps.product_id
LEFT JOIN preorder.inventory_arrival  ia ON ia.product_id = ps.product_id

WHERE ps.status IN ('active_preorder', 'historical_preorder');