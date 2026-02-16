# read_layer.py

from typing import List, Optional, Dict, Any


TABLE_NAME = "preorder.product_status"


def _base_query(supabase):
    return supabase.table(TABLE_NAME)


def get_product_status(supabase, product_id: int) -> Optional[Dict[str, Any]]:
    response = (
        _base_query(supabase)
        .select("*")
        .eq("product_id", product_id)
        .execute()
    )

    data = response.data or []
    return data[0] if data else None


def get_all_products(supabase) -> List[Dict[str, Any]]:
    response = _base_query(supabase).select("*").execute()
    return response.data or []


def get_by_status(supabase, status: str) -> List[Dict[str, Any]]:
    response = (
        _base_query(supabase)
        .select("*")
        .eq("status", status)
        .execute()
    )
    return response.data or []


def get_anomalies(supabase) -> List[Dict[str, Any]]:
    response = (
        _base_query(supabase)
        .select("*")
        .like("status", "anomaly_%")
        .execute()
    )
    return response.data or []


def get_active_preorders(supabase) -> List[Dict[str, Any]]:
    return get_by_status(supabase, "active_preorder")


def get_early_stock_arrivals(supabase) -> List[Dict[str, Any]]:
    return get_by_status(supabase, "early_stock_arrival")