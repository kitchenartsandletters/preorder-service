from typing import AsyncGenerator
from shopify_client import ShopifyClient
from services.supabase_client import get_client as get_supabase_client_raw


# ---- Shopify ----

async def get_shopify_client() -> AsyncGenerator[ShopifyClient, None]:
    """
    Per-request Shopify client.
    Automatically closes after request lifecycle.
    """
    client = ShopifyClient()
    try:
        yield client
    finally:
        await client.close()


# ---- Supabase ----

def get_supabase_client():
    """
    Supabase is a singleton (service role client).
    No need to close per request.
    """
    return get_supabase_client_raw()


import os
from fastapi import Header, HTTPException

def require_admin_key(x_admin_key: str = Header(default=None)):
    expected = os.getenv("RECLASSIFY_ADMIN_KEY")

    if not expected:
        raise RuntimeError("RECLASSIFY_ADMIN_KEY not configured")

    if x_admin_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

    return True