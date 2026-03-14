
-- drop views in dependency order
-- metrics depends on release_queue
drop view if exists preorder.vw_preorder_metrics;

-- release_candidates depends on release_queue
 drop view if exists preorder.vw_preorder_release_candidates;

-- release_queue depends on products
 drop view if exists preorder.vw_preorder_release_queue;

-- base view
 drop view if exists preorder.vw_preorder_products;

-- recreate base view
create view preorder.vw_preorder_products as
select
    ps.product_id,

    ps.metadata_snapshot->>'title' as title,
    ps.metadata_snapshot->>'isbn' as isbn,

    greatest((ps.metadata_snapshot->>'inventory')::int, 0) as inventory,
    
    coalesce(cl.presold_qty, 0) as presold_qty,

    ps.effective_pub_date as pub_date,

    ps.status as classification,

    (ps.metadata_snapshot->>'preorder_tag_present')::boolean
        as preorder_tag_present,

    (ps.metadata_snapshot->>'in_preorder_collection')::boolean
        as preorder_collection_present,

    case
        when po.product_id is not null then 'override'
        else 'none'
    end as override_status,

    ps.anomaly_type,

    ps.last_classified_at as last_updated

from preorder.product_status ps

left join (
    select
        product_id,
        sum(delta_qty) as presold_qty
    from preorder.commitment_ledger
    where delta_qty > 0
    group by product_id
) cl
    on cl.product_id = ps.product_id

left join preorder.product_overrides po
    on po.product_id = ps.product_id;


-- recreate dependent view
create view preorder.vw_preorder_release_queue as
select
    v.product_id,
    v.title,
    v.isbn,
    v.inventory,
    v.presold_qty,
    v.pub_date,
    v.classification,
    v.preorder_tag_present,
    v.preorder_collection_present,
    v.override_status,
    v.anomaly_type,
    v.last_updated,

    case
        when v.classification = 'active_preorder'
             and v.pub_date is not null
             and v.pub_date <= current_date + interval '7 days'
        then true
        else false
    end as due_for_release_review,

    case
        when v.inventory > 0
             and v.classification = 'active_preorder'
             and (v.pub_date is null or v.pub_date > current_date)
        then true
        else false
    end as early_stock_arrival

from preorder.vw_preorder_products v;

-- dashboard metrics view
create view preorder.vw_preorder_metrics as
select
    count(*) filter (where classification = 'active_preorder')
        as active_preorders,

    count(*) filter (where early_stock_arrival = true)
        as early_arrivals,

    count(*) filter (
        where due_for_release_review = true
    ) as releases_due_for_review,

    sum(presold_qty) filter (
        where classification = 'active_preorder'
    ) as total_presold_units,

    count(*) filter (
        where classification = 'active_preorder'
        and pub_date >= current_date
        and pub_date < current_date + interval '7 days'
    ) as releases_this_week

from preorder.vw_preorder_release_queue;