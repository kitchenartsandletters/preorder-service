import pytest
from unittest.mock import AsyncMock, MagicMock

from domain_models import ProductMetadata
from shopify_service import (
    build_product_metadata_from_shopify,
)


@pytest.fixture
def mock_shopify_client(monkeypatch):
    mock = AsyncMock()

    # Patch the module-level shopify_client instance used by shopify_service
    import shopify_service
    monkeypatch.setattr(shopify_service, "shopify_client", mock, raising=False)

    return mock


# ---------------------------------------------------------
# PRODUCT ID PATH
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_build_metadata_from_product_id_basic(mock_shopify_client):
    mock_shopify_client.graphql.return_value = {
        "product": {
            "id": "gid://shopify/Product/123",
            "handle": "test-book",
            "tags": ["preorder", "2026-04-01"],
            "collections": {
                "edges": [
                    {"node": {"handle": "preorder"}}
                ]
            },
            "metafield": {
                "value": "2026-05-01"
            },
            "variants": {
                "edges": [
                    {"node": {"inventoryQuantity": 5}},
                    {"node": {"inventoryQuantity": 3}},
                ]
            }
        }
    }

    metadata = await build_product_metadata_from_shopify(product_id=123)

    assert isinstance(metadata, ProductMetadata)
    assert metadata.product_id == 123
    assert metadata.in_preorder_collection is True
    assert "preorder" in metadata.tags
    assert metadata.override_date_raw == "2026-05-01"
    assert metadata.inventory == 8
    assert "2026-04-01" in metadata.date_tags_raw


# ---------------------------------------------------------
# COLLECTION ABSENT
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_collection_missing_sets_false(mock_shopify_client):
    mock_shopify_client.graphql.return_value = {
        "product": {
            "id": "gid://shopify/Product/123",
            "handle": "test-book",
            "tags": ["preorder"],
            "collections": {"edges": []},
            "metafield": None,
            "variants": {"edges": []}
        }
    }

    metadata = await build_product_metadata_from_shopify(product_id=123)

    assert metadata.in_preorder_collection is False


# ---------------------------------------------------------
# INVENTORY ITEM PATH
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_build_metadata_from_inventory_item_id(mock_shopify_client):
    # First call resolves product id
    mock_shopify_client.graphql.side_effect = [
        {
            "inventoryItem": {
                "variant": {
                    "product": {
                        "id": "gid://shopify/Product/999"
                    }
                }
            }
        },
        {
            "product": {
                "id": "gid://shopify/Product/999",
                "handle": "inventory-test",
                "tags": ["preorder", "2027-01-01"],
                "collections": {
                    "edges": [
                        {"node": {"handle": "preorder"}}
                    ]
                },
                "metafield": None,
                "variants": {
                    "edges": [
                        {"node": {"inventoryQuantity": 2}}
                    ]
                }
            }
        }
    ]

    metadata = await build_product_metadata_from_shopify(inventory_item_id=555)

    assert metadata.product_id == 999
    assert metadata.inventory == 2
    assert metadata.in_preorder_collection is True


# ---------------------------------------------------------
# METAFIELD ABSENT
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_override_metafield_none(mock_shopify_client):
    mock_shopify_client.graphql.return_value = {
        "product": {
            "id": "gid://shopify/Product/123",
            "handle": "test-book",
            "tags": ["preorder"],
            "collections": {"edges": []},
            "metafield": None,
            "variants": {"edges": []}
        }
    }

    metadata = await build_product_metadata_from_shopify(product_id=123)

    assert metadata.override_date_raw is None