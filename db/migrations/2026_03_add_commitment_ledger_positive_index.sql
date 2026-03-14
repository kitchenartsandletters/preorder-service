create index idx_commitment_ledger_positive
on preorder.commitment_ledger (product_id)
where delta_qty > 0;