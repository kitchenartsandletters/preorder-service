"""
shopify_version.py — the single source of truth for the Shopify Admin API version.

Every Admin API URL builder in this repo must call `get_api_version()` rather
than reading env or hard-coding a version. `tests/test_shopify_api_version.py`
fails if a version literal appears anywhere else in the Python code.

Resolution order
----------------
1. `SHOPIFY_API_VERSION` (the canonical env var; set in Railway)
2. `API_VERSION`         (legacy name, still passed by the weekly GitHub
                          workflow — see docs/DOCS_STATUS.md Landmine 1)
3. `DEFAULT_SHOPIFY_API_VERSION` below

IMPORTANT: in production the env var wins. Changing the default below does NOT
change the version production uses while `SHOPIFY_API_VERSION` is set in
Railway. Each Railway service (web and any cron/job services) has its own
variables and must be updated individually.

Why this module exists
----------------------
Before it, six call sites resolved the version independently with three
different defaults ("2025-10", "2025-01", and the legacy `API_VERSION` name),
so processes could silently run on different versions.

Version support
---------------
Shopify supports each stable version for at least 12 months; an app that
requests an unsupported version is served the oldest supported stable version
instead. Check https://shopify.dev/docs/api/usage/versioning before bumping.
"""

from __future__ import annotations

import os

# Latest stable as of 2026-09-19; supported until at least 2027-07-01 15:00 UTC.
DEFAULT_SHOPIFY_API_VERSION = "2026-07"


def get_api_version() -> str:
    """Return the Shopify Admin API version to use for this process."""
    return (
        os.getenv("SHOPIFY_API_VERSION")
        or os.getenv("API_VERSION")
        or DEFAULT_SHOPIFY_API_VERSION
    )
