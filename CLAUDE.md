# CLAUDE.md — preorder-service

Conventions for AI assistants (Claude Code and chat) working in this repo. Keep it short and current.

## Branches & deploys
- **`main` is the live branch.** Railway auto-deploys on merge to `main`; the branch Railway builds is configured in the Railway dashboard, not in this repo. Changes reach production by merging to `main`.
- **Open every PR against `main`.** Work on short-lived feature branches off `main` and delete them after merge.

## Stack (orientation)
- FastAPI, Python 3.13. Supabase (schema `preorder`) holds the read models and state; Shopify Admin GraphQL API. The version resolves only through `shopify_version.get_api_version()` (never hard-code one; a test enforces this). In production the `SHOPIFY_API_VERSION` variable on each Railway service wins over the code default. Before any version bump, run `scripts/smoke_shopify_api_version.py` (read-only, differential).
- Admin endpoints authenticate with the `X-Admin-Token` header.
- The week-based shipping-profile system lives in `services/` (`shipping_profiles.py`, `release_week.py`, `week_profiles.py`, `week_migration.py`, `week_reconcile.py`), exposed via `routes/admin_shipping.py` under `/admin/preorders/shipping/profiles`. The `admin-dashboard` frontend renders these read-only — it does not recompute preorder logic.
- Tests are plain `pytest` under `tests/` (e.g. `test_week_reconcile.py`, `test_week_migration.py`). Install test deps with `pip install -r requirements-dev.txt` (not in the production image), then `python -m pytest`. The suite runs offline: `tests/conftest.py` supplies placeholder env, and `tests/fakes.py` provides an in-memory `FakeSupabase` that mirrors production's `.schema(...).table(...)` call chains — use it instead of MagicMock for Supabase-touching code.
