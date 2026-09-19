"""
conftest.py

Global pytest configuration and reusable fixtures.

Offline-import defaults
-----------------------
Importing `main` (e.g. for FastAPI TestClient tests) pulls in modules that read
env at import time:
- Route modules `routes/admin_preorders.py`, `admin_nyt.py`,
  `admin_shipping.py`, `admin_tagger.py`, `admin_cleanup.py` call
  `supabase.create_client(...)` at import instead of using the lazy
  `services/supabase_client.get_client()`.
- `jobs/mailtrap.py`, `jobs/order_tagger.py`, `jobs/nyt_notifier.py` and
  `jobs/nyt_reporter.py` read required vars with `os.environ[...]` at module
  level.

We set harmless placeholder values with `os.environ.setdefault` so:
- tests can import `main` offline (no network: supabase-py 2.x does not
  connect at client construction);
- a developer's real environment still wins if already set.

This is a test-side accommodation only. Converting those modules to the lazy
client is a separate production change, not part of test-infra work.
"""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("SHOP_URL", "test-shop.myshopify.com")
os.environ.setdefault("MAILTRAP_API_TOKEN", "test-mailtrap-token")
os.environ.setdefault("EMAIL_SENDER", "test-sender@example.com")
os.environ.setdefault("EMAIL_RECIPIENTS", "test-recipient@example.com")
os.environ.setdefault("NYT_PORTAL_USERNAME", "test-nyt-user")
os.environ.setdefault("NYT_PORTAL_PASSWORD", "test-nyt-password")

import pytest  # noqa: E402

from classification.types import ClassificationInput  # noqa: E402,F401

from tests.fakes import FakeSupabase  # noqa: E402


@pytest.fixture
def fake_supabase() -> FakeSupabase:
    """Fresh in-memory Supabase stand-in (see tests/fakes.py)."""
    return FakeSupabase()
