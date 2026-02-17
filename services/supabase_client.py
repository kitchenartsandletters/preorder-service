import os
from supabase import create_client, Client
from typing import Optional


_supabase: Optional[Client] = None


def get_client() -> Client:
    """
    Lazy-initialize Supabase client.

    This prevents import-time crashes during testing.
    """

    global _supabase

    if _supabase is not None:
        return _supabase

    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "Supabase not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY env vars."
        )

    _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _supabase