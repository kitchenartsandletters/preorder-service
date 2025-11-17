-- 1. Add product-level state columns
alter table preorder.tracking
    add column product_status text,
    add column anomaly_type text,
    add column effective_pub_date date,
    add column last_classified_at timestamptz;

-- 2. Optional product snapshot
alter table preorder.tracking
    add column product_snapshot jsonb;

-- 3. Helpful indexes
create index if not exists idx_preorder_product_id
    on preorder.tracking(product_id);

create index if not exists idx_preorder_status
    on preorder.tracking(product_status);

create index if not exists idx_preorder_anomaly
    on preorder.tracking(anomaly_type);