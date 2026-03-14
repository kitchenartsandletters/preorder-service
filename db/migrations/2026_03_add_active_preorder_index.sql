create index idx_product_status_active_preorders
on preorder.product_status (effective_pub_date)
where status = 'active_preorder';