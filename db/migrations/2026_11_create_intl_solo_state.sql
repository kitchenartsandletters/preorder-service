-- 2026_11_create_intl_solo_state.sql
-- International cart isolation ("intl-solo"), PR 1. NOT YET APPLIED.
--
-- Apply manually in Supabase before deploying the code that reads it
-- (routes/admin_intl_solo.py). Purely additive: two new tables and one new
-- view. It does NOT touch vw_preorder_products, so it cannot collide with the
-- pending Move 1g view rewrite (2026_10_move1g_retire_override.sql).
--
-- WHAT intl-solo IS
-- The Shopify product tag `intl-solo` is what the Cart Lock checkout rule
-- enforces: an order shipping outside the US that contains an intl-solo
-- product may contain only that one product (any quantity). Market orders
-- ship whole and cannot be split without voiding prepaid duties, so a title
-- that cannot ship yet must travel alone.
--
-- LIFECYCLE (owner decisions, 2026-11-09)
-- intl-solo is independent of the domestic preorder lifecycle:
--   * Restricted automatically when a title becomes a preorder.
--   * Stays restricted through its pub date, Full Cleanup, holds, and any
--     domestic status change. Full Cleanup must never remove it.
--   * Cleared ONLY by a manual decision in the dashboard, on one of two bases:
--       received    stock has physically arrived (inventory_arrival row)
--       guaranteed  stock is guaranteed before it arrives
--     Early arrivals (stock before pub date) are reviewed case by case.
--   * force_on restricts a title that is not a preorder.
--
-- The Shopify tag is the enforcement. This table is the decision of record:
-- what the tag SHOULD be and why. A mismatch between the two is drift.

create table if not exists preorder.intl_solo_state (
    product_id  bigint      primary key,
    state       text        not null check (state in ('restricted', 'cleared')),
    basis       text        not null check (basis in ('auto_preorder', 'force_on', 'received', 'guaranteed')),
    reason      text,
    changed_by  text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint intl_solo_state_basis_matches_state check (
        (state = 'restricted' and basis in ('auto_preorder', 'force_on'))
        or (state = 'cleared' and basis in ('received', 'guaranteed'))
    ),
    -- Every human decision carries a reason; only the automatic one may not.
    constraint intl_solo_state_reason_required check (
        basis = 'auto_preorder'
        or (reason is not null and length(btrim(reason)) > 0)
    )
);

comment on table preorder.intl_solo_state is
    'Decision of record for the intl-solo Shopify tag. See 2026_11_create_intl_solo_state.sql.';

-- Append-only audit trail, modelled on preorder.cleanup_log.
create table if not exists preorder.intl_solo_log (
    id           bigserial   primary key,
    product_id   bigint      not null,
    action       text        not null,  -- restrict | clear | restore | tag_add | tag_remove | drift
    from_state   text,
    to_state     text,
    basis        text,
    reason       text,
    changed_by   text,
    success      boolean     not null default true,
    details      jsonb       not null default '{}'::jsonb,
    errors       jsonb       not null default '[]'::jsonb,
    performed_at timestamptz not null default now()
);

create index if not exists intl_solo_log_product_idx
    on preorder.intl_solo_log (product_id, performed_at desc);

-- Raw inputs for every product the intl-solo module cares about. No derived
-- state here: services/intl_solo_service.py derives it, as a pure, tested
-- function, so the SQL and Python cannot disagree.
--
-- intl_solo_tag_present comes from product_status.metadata_snapshot, i.e. the
-- tags as of the last classification (snapshot_at), not a live Shopify read.
-- Tag comparison is case-insensitive.
create or replace view preorder.vw_intl_solo_inputs as
 SELECT ps.product_id,
    ps.metadata_snapshot ->> 'title'::text AS title,
    ps.metadata_snapshot ->> 'isbn'::text AS isbn,
    ps.status AS classification,
    ps.effective_pub_date AS pub_date,
    COALESCE((ps.metadata_snapshot ->> 'in_preorder_collection'::text)::boolean, false) AS in_preorder_collection,
    tag.present AS intl_solo_tag_present,
    ps.last_classified_at AS snapshot_at,
    ia.first_positive_inventory_at,
    s.state,
    s.basis,
    s.reason,
    s.changed_by,
    s.updated_at AS state_updated_at
   FROM preorder.product_status ps
     CROSS JOIN LATERAL ( SELECT (EXISTS ( SELECT 1
                   FROM jsonb_array_elements_text(
                        CASE
                            WHEN jsonb_typeof(ps.metadata_snapshot -> 'tags'::text) = 'array'::text THEN ps.metadata_snapshot -> 'tags'::text
                            ELSE '[]'::jsonb
                        END) t(tag)
                  WHERE lower(btrim(t.tag)) = 'intl-solo'::text)) AS present) tag
     LEFT JOIN preorder.inventory_arrival ia ON ia.product_id = ps.product_id
     LEFT JOIN preorder.intl_solo_state s ON s.product_id = ps.product_id
  WHERE s.product_id IS NOT NULL
     OR ps.status = ANY (ARRAY['active_preorder'::text, 'early_stock_arrival'::text])
     OR COALESCE((ps.metadata_snapshot ->> 'in_preorder_collection'::text)::boolean, false)
     OR tag.present;

comment on view preorder.vw_intl_solo_inputs is
    'Raw inputs for intl-solo state derivation (services/intl_solo_service.py). Tags are as of snapshot_at.';
