-- 2026_10_create_preorder_hold.sql
--
-- Explicit preorder holds.
--
-- Background: the engine inferred a "delayed import hold" from (past pub date
-- AND no stock arrival) and kept such a title active_preorder indefinitely.
-- Owner decision (2026-10-15): that inference is wrong. Once a pub date passes
-- and stock has not arrived, a title is a historical preorder with unresolved
-- commitments — UNLESS someone deliberately holds it:
--
--   event          a signing or similar; the preorder incentive runs through
--                  the event date
--   awaiting_stock stock genuinely in transit; keep the preorder presentation
--                  while customers wait
--
-- A hold is what keeps a title in the Preorder collection past its pub date.
-- Collection membership drives the rewards system, so the hold is literally
-- what keeps the incentive and the preorder copy live.
--
-- Expiry: hold_until is the LAST held day, evaluated in America/New_York. The
-- rest of this system is ET (lifecycle snapshotter, reporting weeks) while the
-- classifier uses the container's date; evaluating here in ET, once, keeps the
-- dashboard label and the backend from disagreeing for part of a day.
--
-- One row per product: setting a hold replaces any existing one. There is no
-- hold history table yet; add one if an audit trail is needed beyond
-- created_by/updated_at.

create table if not exists preorder.preorder_hold (
    product_id  bigint      primary key,
    hold_until  date        not null,
    reason      text        not null check (reason in ('event', 'awaiting_stock')),
    note        text,
    created_by  text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

comment on table preorder.preorder_hold is
    'Explicit, expiring holds that keep a past-dated title an active preorder. See 2026_10_create_preorder_hold.sql.';
comment on column preorder.preorder_hold.hold_until is
    'Last held day, inclusive, evaluated in America/New_York.';
comment on column preorder.preorder_hold.note is
    'Free text shown in the dashboard, e.g. "signing with the author".';

create index if not exists preorder_hold_until_idx
    on preorder.preorder_hold (hold_until);

-- Expose the hold on the dashboard's products view.
-- hold_active is evaluated here so no consumer does its own date arithmetic.
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
        CASE
            WHEN po.product_id IS NOT NULL THEN 'override'::text
            ELSE 'none'::text
        END AS override_status,
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
     LEFT JOIN preorder.product_overrides po ON po.product_id = ps.product_id
     LEFT JOIN preorder.vw_arrival_timing at_view ON at_view.product_id = ps.product_id
     LEFT JOIN preorder.inventory_arrival ia ON ia.product_id = ps.product_id
     LEFT JOIN preorder.lifecycle_snapshot ls ON ls.product_id = ps.product_id
     LEFT JOIN preorder.release_state rs ON rs.product_id = ps.product_id AND rs.released_to_reporting = true
     LEFT JOIN preorder.preorder_hold h ON h.product_id = ps.product_id;
