from unittest.mock import MagicMock
from read_layer import (
    get_product_status,
    get_all_products,
    get_by_status,
    get_anomalies,
    get_active_preorders,
)


def mock_response(data):
    m = MagicMock()
    m.data = data
    return m


def test_get_product_status_returns_single_record():
    supabase = MagicMock()
    supabase.table().select().eq().execute.return_value = mock_response(
        [{"product_id": 1}]
    )

    result = get_product_status(supabase, 1)
    assert result["product_id"] == 1


def test_get_all_products_returns_list():
    supabase = MagicMock()
    supabase.table().select().execute.return_value = mock_response(
        [{"product_id": 1}, {"product_id": 2}]
    )

    result = get_all_products(supabase)
    assert len(result) == 2


def test_get_by_status_filters_correctly():
    supabase = MagicMock()
    supabase.table().select().eq().execute.return_value = mock_response(
        [{"status": "active_preorder"}]
    )

    result = get_by_status(supabase, "active_preorder")
    assert result[0]["status"] == "active_preorder"


def test_get_anomalies_uses_like():
    supabase = MagicMock()
    supabase.table().select().like().execute.return_value = mock_response(
        [{"status": "anomaly_missing_tag"}]
    )

    result = get_anomalies(supabase)
    assert result[0]["status"].startswith("anomaly_")


def test_get_active_preorders_delegates():
    supabase = MagicMock()
    supabase.table().select().eq().execute.return_value = mock_response(
        [{"status": "active_preorder"}]
    )

    result = get_active_preorders(supabase)
    assert result[0]["status"] == "active_preorder"