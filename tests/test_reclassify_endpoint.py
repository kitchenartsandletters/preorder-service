import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from main import app

from routes import reclassify
from dependencies import get_supabase_client, get_shopify_client

import logging

logger = logging.getLogger(__name__)

# ---- Setup Test Client ----

client = TestClient(app)


# ---- Dependency Overrides ----

@pytest.fixture(autouse=True)
def override_dependencies():
    app.dependency_overrides[get_supabase_client] = lambda: AsyncMock()
    app.dependency_overrides[get_shopify_client] = lambda: AsyncMock()
    app.dependency_overrides[reclassify.require_admin_key] = lambda: True
    yield
    app.dependency_overrides.clear()


# ---- Single Product Endpoint ----

def test_reclassify_single_success(monkeypatch):

    from routes import reclassify

    monkeypatch.setattr(
        reclassify,
        "reclassify_single_product",
        AsyncMock(return_value={
            "product_id": 123,
            "status": "active_preorder",
            "anomaly_type": None,
            "effective_pub_date": "2026-04-01",
            "engine_version": "v1",
        })
    )

    response = client.post("/reclassify/123")

    assert response.status_code == 200
    data = response.json()
    assert data["product_id"] == 123
    assert data["status"] == "active_preorder"

    logger.info("Reclassify single triggered", extra={"product_id": 123})

# ---- Batch Endpoint ----

def test_reclassify_batch_success(override_dependencies, monkeypatch):

    from routes import reclassify

    async def success_mock(*args, **kwargs):
        return {
            "product_id": kwargs["product_id"],
            "status": "active_preorder",
            "anomaly_type": None,
            "effective_pub_date": "2026-04-01",
            "engine_version": "v1",
        }

    monkeypatch.setattr(
        reclassify,
        "reclassify_single_product",
        success_mock
    )

    response = client.post(
        "/reclassify/batch",
        json={"product_ids": [1, 2, 3]}
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total_requested"] == 3
    assert data["total_processed"] == 3
    assert len(data["results"]) == 3

    logger.info("Reclassify batch triggered", extra={"count": len(data["results"])})


# ---- Error Handling ----

def test_reclassify_single_failure(monkeypatch):

    from routes import reclassify

    async def failing_mock(*args, **kwargs):
        raise Exception("boom")

    monkeypatch.setattr(
        reclassify,
        "reclassify_single_product",
        failing_mock
    )

    response = client.post("/reclassify/123")

    assert response.status_code == 400
    assert "boom" in response.json()["detail"]