-- 2026_11_preorder_run.sql
--
-- Declared preorder runs. The first half of the run model proposed under
-- DOCS_STATUS Landmine 20: run IDENTITY, without re-keying lifecycle_snapshot.
--
-- WHY DECLARED AND NOT DERIVED
-- ----------------------------
-- vw_run_state shipped with a derived signal: a closed lifecycle whose pub date
-- now postdates its own closure. That caught Septime (English) and cannot catch
-- A Very Serious Cookbook: Contra Wildair, whose original run was 2018 — before
-- this system existed, so there is no snapshot to derive from. Both are limited
-- reissues of out-of-print editions. One was visible only by accident.
--
-- Inference was measured and rejected on 2026-11-24. Across the whole of
-- pubdate_history there are 5 forward date jumps over a year, on 3 products:
--
--   Contra Wildair   2018-10-12 -> 2026-12-07   2,978 days  shopify_direct_edit
--   Septime          2021-10-20 -> 2025-09-30   1,441 days  override_date
--   Septime          2021-10-20 -> 2025-09-30   1,441 days  override_migration
--   Septime          2025-09-30 -> 2026-12-16     442 days  ui_change
--   Animal, Vegetable, Junk (pbk)
--                    2021-02-02 -> 2022-04-26     448 days  shopify_pub_date
--
-- Two of the five are bookkeeping (the retired override, and the Move 1d fold).
-- One is a false positive: a paperback edition's date correction, and the
-- product is not_a_preorder_product. And change_source cannot rescue the
-- heuristic, because the one genuine reissue is labelled shopify_direct_edit —
-- the same label any unmanaged edit gets.
--
-- This repo settled the principle once already. The inferred delayed-import
-- hold was replaced on 2026-10-15 with an explicit, expiring hold, on the
-- owner's ruling that "that inference is wrong". A reissue is the same kind of
-- thing: a commercial decision somebody makes, not a shape in the data.
--
-- SCOPE (owner, 2026-11-24)
-- -------------------------
-- Table and view only. SQL-declared for now; no admin endpoint until a third
-- title needs one. lifecycle_snapshot is NOT re-keyed, so a second run still
-- has no frozen cohort and fetch_banked_presales still reads 0 for such a
-- title. The instruction not to queue Septime for release STANDS.
--
-- Reverse with:
--   drop table if exists preorder.preorder_run;
--   then re-apply 2026_11_run_aware_read_layer.sql to restore the derived-only view.

create table if not exists preorder.preorder_run (
    product_id       bigint      not null,
    run_no           int         not null check (run_no >= 1),
    -- First day of the run, inclusive, evaluated in America/New_York to match
    -- preorder_hold.hold_until and the lifecycle snapshotter. Only the CURRENT
    -- run's date is used for scoping; earlier rows are documentation.
    run_started_on   date        not null,
    label            text,
    note             text,
    -- True when the run happened before this system recorded anything, so
    -- there is no ledger or snapshot history for it. Lets a consumer say
    -- "not recorded" instead of showing an empty panel or a misleading zero.
    predates_system  boolean     not null default false,
    created_by       text,
    created_at       timestamptz not null default now(),
    primary key (product_id, run_no)
);

comment on table preorder.preorder_run is
    'Declared preorder runs. A limited reissue is a second run on the same '
    'product and ISBN. See 2026_11_preorder_run.sql and DOCS_STATUS Landmine 20.';
comment on column preorder.preorder_run.run_started_on is
    'First day of the run, inclusive, in America/New_York.';
comment on column preorder.preorder_run.predates_system is
    'The run has no ledger or snapshot history because it predates this system.';

-- --------------------------------------------------------------------------
-- The declared runs as of 2026-11-24.
-- --------------------------------------------------------------------------

insert into preorder.preorder_run
    (product_id, run_no, run_started_on, label, note, predates_system, created_by)
values
    -- A Very Serious Cookbook: Contra Wildair
    (5238867361925, 1, '2018-10-12', 'original edition',
     'Original publication. Predates this system: no ledger or snapshot history exists, '
     'so prior-run figures are unavailable rather than zero.', true, 'owner 2026-11-24'),
    (5238867361925, 2, '2026-11-13', 'limited reissue',
     'Publisher reprint of an out-of-print edition. Ships 2026-12-07. Run start is the '
     'first reissue order; the ledger holds nothing for this product before it.',
     false, 'owner 2026-11-24'),

    -- Septime (English)
    (6598890029189, 1, '2021-08-25', 'original edition',
     'First recorded order (backfill). Original pub date 2021-10-20, later revised to '
     '2025-09-30 by the retired override. Lifecycle closed 2026-03-31.',
     false, 'owner 2026-11-24'),
    (6598890029189, 2, '2026-03-31', 'limited reissue',
     'Publisher reprint of an out-of-print edition. Ships 2026-12-16. Run start is the '
     'instant run 1 closed, so the timeline has no gap and the boundary matches what '
     'the derived signal computed.', false, 'owner 2026-11-24')
on conflict (product_id, run_no) do nothing;

-- --------------------------------------------------------------------------
-- vw_run_state, now declaration-first.
--
-- Column order and names are preserved: admin-dashboard PR #129 consumes this
-- view through GET /admin/preorders/run-state and reads the rows unchanged.
-- New columns are added at the end.
--
-- has_prior_closed_run BROADENS rather than changes meaning. It was only ever
-- false for Contra Wildair because the prior run was invisible to us; the run
-- did happen and did close. The column now finally means what its name says,
-- so the dashboard needs no change to badge both titles.
-- --------------------------------------------------------------------------

create or replace view preorder.vw_run_state as
with declared as (
    -- The current declared run is the highest run_no per product.
    select distinct on (product_id)
        product_id,
        run_no          as current_run_no,
        run_started_on  as current_run_started_on
    from preorder.preorder_run
    order by product_id, run_no desc
),
prior_declared as (
    -- The run immediately before the current one.
    select distinct on (r.product_id)
        r.product_id,
        r.run_no          as prior_run_no,
        r.run_started_on  as prior_run_started_on,
        r.label           as prior_run_label,
        r.note            as prior_run_note,
        r.predates_system as prior_run_predates_system
    from preorder.preorder_run r
    join declared d
      on d.product_id = r.product_id
     and r.run_no < d.current_run_no
    order by r.product_id, r.run_no desc
),
boundary as (
    select
        ps.product_id,
        ps.metadata_snapshot ->> 'title'   as title,
        ps.metadata_snapshot ->> 'isbn'    as isbn,
        ps.status                          as classification,
        ps.effective_pub_date,
        ls.lifecycle_closed_at,
        ls.presale_commitment_total,
        (ps.metadata_snapshot ->> 'inventory')::integer as inventory_unclamped,
        d.current_run_no,
        pd.prior_run_no,
        pd.prior_run_label,
        pd.prior_run_note,
        pd.prior_run_predates_system,
        -- A declared run beats the derived signal. The derived one stays as a
        -- fallback so an undeclared reissue with a recorded closed run is still
        -- caught, which is how Septime was found in the first place.
        (
            pd.product_id is not null
            or (
                ls.lifecycle_closed_at is not null
                and ps.effective_pub_date is not null
                and ps.effective_pub_date > ls.lifecycle_closed_at::date
            )
        ) as has_prior_closed_run,
        case
            when d.product_id is not null then 'declared'
            when ls.lifecycle_closed_at is not null
                 and ps.effective_pub_date is not null
                 and ps.effective_pub_date > ls.lifecycle_closed_at::date then 'derived'
            else 'none'
        end as run_source,
        -- The scoping instant for current-run figures.
        --   declared -> ET midnight of run_started_on
        --   derived  -> the prior lifecycle's closure
        --   none     -> null, meaning all history
        -- `::timestamp AT TIME ZONE` interprets a naive timestamp IN New York
        -- and yields timestamptz, which is the correct direction. Note
        -- vw_live_presale_metrics casts to timestamptz FIRST and then applies
        -- AT TIME ZONE, which strips the zone instead; do not copy that.
        case
            when d.current_run_started_on is not null
                then (d.current_run_started_on::timestamp at time zone 'America/New_York')
            when ls.lifecycle_closed_at is not null
                 and ps.effective_pub_date is not null
                 and ps.effective_pub_date > ls.lifecycle_closed_at::date
                then ls.lifecycle_closed_at
        end as current_run_from
    from preorder.product_status ps
    left join preorder.lifecycle_snapshot ls on ls.product_id = ps.product_id
    left join declared d                     on d.product_id  = ps.product_id
    left join prior_declared pd              on pd.product_id = ps.product_id
    where ps.status <> 'not_a_preorder_product'
),
per_line as (
    -- Netted per LINE, never per order: per-order netting hides a live line
    -- beside a shipped one, which is the Landmine 14 shape.
    select
        b.product_id,
        cl.order_id,
        cl.line_item_id,
        sum(cl.delta_qty) as net
    from boundary b
    join preorder.commitment_ledger cl
        on cl.product_id = b.product_id
    where cl.topic in ('orders/create', 'orders/fulfilled', 'refunds/create')
      and (b.current_run_from is null or cl.occurred_at >= b.current_run_from)
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

    b.has_prior_closed_run,
    b.lifecycle_closed_at,
    b.current_run_from as current_run_started_at,

    -- Null unless there is a prior run, so these cannot be rendered as current.
    -- Also null when the prior run predates the system: unavailable, not zero.
    case
        when not b.has_prior_closed_run then null
        when coalesce(b.prior_run_predates_system, false) then null
        else b.presale_commitment_total
    end as prior_run_presale_total,
    -- Null, not false, when the prior run predates the system: we cannot say it
    -- was unreported, only that we have no record either way.
    case
        when not b.has_prior_closed_run then null
        when coalesce(b.prior_run_predates_system, false) then null
        else coalesce(r.ever_reported, false)
    end as prior_run_reported,

    coalesce(c.open_lines, 0)   as current_run_open_lines,
    coalesce(c.open_units, 0)   as current_run_open_units,
    coalesce(c.open_orders, 0)  as current_run_open_orders,

    case
        when b.has_prior_closed_run then
            coalesce(r.last_uploaded_at > b.current_run_from, false)
        else coalesce(r.ever_reported, false)
    end as current_run_reported,

    coalesce(c.orphan_lines, 0) as orphan_lines,

    b.inventory_unclamped,

    -- Added 2026-11-24 with preorder_run. Existing columns above are unchanged.
    b.run_source,
    b.current_run_no,
    b.prior_run_no,
    b.prior_run_label,
    b.prior_run_note,
    coalesce(b.prior_run_predates_system, false) as prior_run_predates_system
from boundary b
left join current_run c on c.product_id = b.product_id
left join reporting   r on r.product_id = b.product_id;

comment on view preorder.vw_run_state is
    'Run-aware read surface. Declaration-first: preorder.preorder_run wins, with '
    'the closed-lifecycle signal as a fallback. run_source says which. See '
    'DOCS_STATUS Landmine 20. Not a substitute for the full run model: a second '
    'run still has no frozen cohort, so fetch_banked_presales reads 0 for one.';
