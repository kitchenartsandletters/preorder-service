-- 2026_11_intl_solo_tag_confirmation.sql
-- intl-solo: remember confirmed tag writes. NOT YET APPLIED.
--
-- Apply BEFORE deploying the code that writes these columns
-- (services/intl_solo_actions.py). Additive and safe to re-run.
--
-- WHY
-- Drift compares the decision with the tags in product_status's
-- classification snapshot, which only refreshes when Shopify's
-- products/update webhook is processed, a few seconds after a tag write.
-- Every dashboard action therefore showed a brief false "Tag missing" or
-- "Unexpected tag". Recording each tag write Shopify accepted lets
-- derive_state trust it until a newer snapshot arrives
-- (services/intl_solo_service.effective_tag).

alter table preorder.intl_solo_state
    add column if not exists tag_confirmed boolean,
    add column if not exists tag_confirmed_at timestamptz;

comment on column preorder.intl_solo_state.tag_confirmed is
    'Value of the intl-solo tag after the last write Shopify accepted. Trusted over the classification snapshot while tag_confirmed_at is newer.';
comment on column preorder.intl_solo_state.tag_confirmed_at is
    'When tag_confirmed was recorded.';

-- Same view as 2026_11_create_intl_solo_state.sql, with the two new columns
-- appended (CREATE OR REPLACE VIEW can only add columns at the end), and
-- security_invoker restated so the replace keeps it (2026_11_intl_solo_rls.sql).
create or replace view preorder.vw_intl_solo_inputs
with (security_invoker = on) as
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
    s.updated_at AS state_updated_at,
    s.tag_confirmed,
    s.tag_confirmed_at
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

-- CREATE OR REPLACE keeps existing grants; restated here so a fresh build
-- matches production.
revoke all on preorder.vw_intl_solo_inputs from anon, authenticated;
grant select on preorder.vw_intl_solo_inputs to service_role;
