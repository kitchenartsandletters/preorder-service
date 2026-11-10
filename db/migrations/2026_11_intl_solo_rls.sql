-- 2026_11_intl_solo_rls.sql
-- Access controls for the intl-solo objects created in
-- 2026_11_create_intl_solo_state.sql. Safe to re-run.
--
-- STATUS
--   Sections 1–3 were APPLIED on 2026-11-09, together with the create
--   migration, via the Supabase connector. This file records them so the
--   repo matches the database.
--   Section 4 (append-only log) was APPLIED on 2026-11-10.
--
-- MODEL: backend only. The service uses the service_role key, which bypasses
-- RLS. RLS on with no policies denies anon and authenticated entirely. This
-- matches the other preorder tables, which grant only to service_role.

-- 1. RLS on, no policies.
alter table preorder.intl_solo_state enable row level security;
alter table preorder.intl_solo_log enable row level security;

-- 2. The view runs with the caller's permissions, so it can't be used to read
--    around RLS (and doesn't trip Supabase's security-definer view advisory).
alter view preorder.vw_intl_solo_inputs set (security_invoker = on);

-- 3. service_role only.
revoke all on preorder.intl_solo_state, preorder.intl_solo_log, preorder.vw_intl_solo_inputs
    from anon, authenticated;
grant select, insert, update, delete on preorder.intl_solo_state to service_role;
grant select, insert on preorder.intl_solo_log to service_role;
grant select on preorder.vw_intl_solo_inputs to service_role;
grant usage, select on sequence preorder.intl_solo_log_id_seq to service_role;

-- 4. Append-only audit log. Supabase's default privileges also gave
--    service_role UPDATE and DELETE on intl_solo_log; the service only ever
--    inserts and selects (services/intl_solo_actions.py), so remove them.
--    Drop this section if you'd rather match preorder.cleanup_log, which keeps
--    full privileges.
revoke update, delete, truncate on preorder.intl_solo_log from service_role;
