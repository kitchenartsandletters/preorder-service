-- 2026_11_run_aware_read_layer.sql
--
-- Adds preorder.vw_run_state: a read surface that distinguishes a title's
-- CURRENT preorder run from a PRIOR closed one.
--
-- WHY
-- ---
-- DOCS_STATUS Landmine 20: preorder.lifecycle_snapshot is PRIMARY KEY
-- (product_id), one lifecycle per product forever. A limited reissue is a
-- SECOND commercial run on the same product and the same ISBN, so its figures
-- have nowhere to live and the prior run's figures are shown as if current.
--
-- Septime (English) made this visible on 2026-11-24. Its dashboard row read
-- Live Presales 47, Est. Presales 454, Data Confidence "estimated",
-- Reporting Status "Reported", lifecycle_closed true — every one of those a
-- run-1 fact, on a title that is an active_preorder with 17 units committed to
-- a reissue. Two fields were not merely stale but self-contradictory:
-- lifecycle_closed true on an active preorder, and arrival_timing
-- "early_arrival" for stock that landed ten months BEFORE the pub date.
--
-- The concrete danger is not cosmetic. A person seeing "Reported" beside an
-- active preorder may queue it for release, and
-- weekly_release_engine.fetch_banked_presales prefers the closed snapshot's
-- presale_commitment_total — which is 0 — so the reissue would be released as
-- though it had no presale cohort at all.
--
-- WHY A NEW VIEW AND NOT COLUMNS ON vw_preorder_products
-- ------------------------------------------------------
-- The staged Move 1g migration (2026_10_move1g_retire_override.sql) does
-- `create or replace view preorder.vw_preorder_products`. Columns added here
-- would be silently dropped the moment Move 1g is applied. A separate view is
-- order-independent against that migration, additive for the frontend, and
-- reversible with a single `drop view`.
--
-- NOT A SUBSTITUTE FOR THE RUN MODEL
-- ----------------------------------
-- This makes the read layer honest; it does not make the data model correct.
-- The run model proposed under Landmine 20 (preorder_run, lifecycle_snapshot
-- keyed (product_id, run_no), cohort scoped by run start) is still the fix.
-- In particular a second run still gets no frozen cohort, so
-- fetch_banked_presales still reads 0 for Septime. The standing instruction
-- not to queue it for release is unchanged by this migration.
--
-- Read-only and additive. No table is touched, no existing view is replaced.
-- Reverse with: drop view if exists preorder.vw_run_state;

create or replace view preorder.vw_run_state as
with boundary as (
    select
        ps.product_id,
        ps.metadata_snapshot ->> 'title'   as title,
        ps.metadata_snapshot ->> 'isbn'    as isbn,
        ps.status                          as classification,
        ps.effective_pub_date,
        ls.lifecycle_closed_at,
        ls.presale_commitment_total,
        (ps.metadata_snapshot ->> 'inventory')::integer as inventory_unclamped,
        -- A closed lifecycle whose pub date now POSTDATES its own closure can
        -- only happen if the title was re-dated for a new run. Deliberately
        -- not conditioned on classification: the signal should hold while the
        -- reissue is still future-dated and after it goes historical.
        (
            ls.lifecycle_closed_at is not null
            and ps.effective_pub_date is not null
            and ps.effective_pub_date > ls.lifecycle_closed_at::date
        ) as has_prior_closed_run
    from preorder.product_status ps
    left join preorder.lifecycle_snapshot ls
        on ls.product_id = ps.product_id
    -- Bounded on purpose: ~211 preorder-relevant rows rather than 15,600.
    where ps.status <> 'not_a_preorder_product'
),
per_line as (
    -- Netted per LINE, never per order. Netting per order would hide a live
    -- line sitting beside a shipped one — the Landmine 14 shape.
    select
        b.product_id,
        cl.order_id,
        cl.line_item_id,
        sum(cl.delta_qty) as net
    from boundary b
    join preorder.commitment_ledger cl
        on cl.product_id = b.product_id
    where cl.topic in ('orders/create', 'orders/fulfilled', 'refunds/create')
      and (
            not b.has_prior_closed_run
            or cl.occurred_at >= b.lifecycle_closed_at
          )
    group by b.product_id, cl.order_id, cl.line_item_id
),
current_run as (
    select
        product_id,
        count(*) filter (where net > 0)                      as open_lines,
        coalesce(sum(net) filter (where net > 0), 0)::bigint as open_units,
        count(distinct order_id) filter (where net > 0)      as open_orders,
        count(*) filter (where net < 0)                      as orphan_lines
    from per_line
    group by product_id
),
reporting as (
    select
        rs.product_id,
        bool_or(rs.nyt_uploaded_at is not null) as ever_reported,
        max(rs.released_at)                     as last_released_at,
        max(rs.nyt_uploaded_at)                 as last_uploaded_at
    from preorder.release_state rs
    group by rs.product_id
)
select
    b.product_id,
    b.title,
    b.isbn,
    b.classification,
    b.effective_pub_date,

    -- Run identity
    b.has_prior_closed_run,
    b.lifecycle_closed_at,
    case when b.has_prior_closed_run then b.lifecycle_closed_at end
        as current_run_started_at,

    -- PRIOR run. Null unless there is one, so a consumer cannot show these as
    -- current by accident.
    case when b.has_prior_closed_run then b.presale_commitment_total end
        as prior_run_presale_total,
    case when b.has_prior_closed_run then coalesce(r.ever_reported, false) end
        as prior_run_reported,

    -- CURRENT run. For a title with no prior run these cover all history, so
    -- the columns mean the same thing for every row.
    coalesce(c.open_lines, 0)   as current_run_open_lines,
    coalesce(c.open_units, 0)   as current_run_open_units,
    coalesce(c.open_orders, 0)  as current_run_open_orders,

    -- Reported WITHIN the current run. This is the field that stops a reissue
    -- inheriting the prior run's "Reported" badge.
    case
        when b.has_prior_closed_run then
            coalesce(r.last_uploaded_at > b.lifecycle_closed_at, false)
        else coalesce(r.ever_reported, false)
    end as current_run_reported,

    -- Residue, surfaced rather than hidden: a negative line is a fulfillment
    -- or refund with no matching create (DOCS_STATUS Landmine 19 / I2).
    coalesce(c.orphan_lines, 0) as orphan_lines,

    -- True oversell. vw_preorder_products.inventory is GREATEST(x, 0) and can
    -- never show a negative (cross-service contract C2), which is useless for
    -- oversell depth; this one is unclamped.
    b.inventory_unclamped
from boundary b
left join current_run c on c.product_id = b.product_id
left join reporting   r on r.product_id = b.product_id;

comment on view preorder.vw_run_state is
    'Run-aware read surface (2026-11-24). Separates a prior closed preorder run '
    'from the current one so run-1 figures cannot pose as current. See '
    'DOCS_STATUS Landmine 20. Additive; does not replace vw_preorder_products. '
    'Not a substitute for the run model: a second run still has no frozen '
    'cohort, so fetch_banked_presales still reads 0 for such a title.';
