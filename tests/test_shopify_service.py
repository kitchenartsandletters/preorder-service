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
                "nodes": [
                    {"handle": "preorder"}
                ]
            },
            "metafields": {
                "nodes": [
                    {"key": "preorder_override_date", "value": "2026-05-01"}
                ]
            },
            "variants": {
                "nodes": [
                    {"inventoryQuantity": 5},
                    {"inventoryQuantity": 3},
                ]
            }
        }
    }

    metadata = await build_product_metadata_from_shopify(
    product_id=123,
    client=mock_shopify_client,
)

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
            "collections": {"nodes": []},
            "metafields": {"nodes": []},
            "variants": {"nodes": []}
        }
    }

    metadata = await build_product_metadata_from_shopify(product_id=123, client=mock_shopify_client)

    assert metadata.in_preorder_collection is False


# ---------------------------------------------------------
# INVENTORY ITEM PATH
#
# Resolution reads the InventoryItem.variants connection. The single-object
# InventoryItem.variant field was deprecated in Admin API 2026-01
# (docs/DOCS_STATUS.md Landmine 12).
# ---------------------------------------------------------

def _product_payload(gid="gid://shopify/Product/999", qty=2):
    return {
        "product": {
            "id": gid,
            "handle": "inventory-test",
            "tags": ["preorder", "2027-01-01"],
            "collections": {"nodes": [{"handle": "preorder"}]},
            "metafields": {"nodes": []},
            "variants": {"nodes": [{"inventoryQuantity": qty}]},
        }
    }


def _inventory_item(*product_gids, shape="nodes"):
    nodes = [{"product": {"id": gid}} for gid in product_gids]
    if shape == "edges":
        return {"inventoryItem": {"variants": {"edges": [{"node": n} for n in nodes]}}}
    return {"inventoryItem": {"variants": {"nodes": nodes}}}


def test_inventory_item_query_uses_variants_connection_not_deprecated_field():
    import re
    from shopify_service import INVENTORY_ITEM_TO_PRODUCT_QUERY as q

    assert re.search(r"\bvariants\s*\(\s*first:\s*\d+\s*\)\s*\{\s*nodes\b", q)
    assert not re.search(r"\bvariant\s*\{", q), "deprecated InventoryItem.variant must not be queried"


@pytest.mark.asyncio
async def test_build_metadata_from_inventory_item_id(mock_shopify_client):
    mock_shopify_client.graphql.side_effect = [
        _inventory_item("gid://shopify/Product/999"),
        _product_payload(),
    ]

    metadata = await build_product_metadata_from_shopify(inventory_item_id=555, client=mock_shopify_client)

    assert metadata.product_id == 999
    assert metadata.inventory == 2
    assert metadata.in_preorder_collection is True

    first_call = mock_shopify_client.graphql.call_args_list[0].kwargs
    assert "variants(first:" in first_call["query"]
    assert first_call["variables"] == {"id": "gid://shopify/InventoryItem/555"}


@pytest.mark.asyncio
async def test_inventory_item_edges_shape_also_resolves(mock_shopify_client):
    mock_shopify_client.graphql.side_effect = [
        _inventory_item("gid://shopify/Product/999", shape="edges"),
        _product_payload(),
    ]

    metadata = await build_product_metadata_from_shopify(inventory_item_id=555, client=mock_shopify_client)

    assert metadata.product_id == 999


@pytest.mark.asyncio
async def test_several_variants_of_one_product_resolve_to_that_product(mock_shopify_client):
    mock_shopify_client.graphql.side_effect = [
        _inventory_item("gid://shopify/Product/999", "gid://shopify/Product/999"),
        _product_payload(),
    ]

    metadata = await build_product_metadata_from_shopify(inventory_item_id=555, client=mock_shopify_client)

    assert metadata.product_id == 999


@pytest.mark.asyncio
async def test_inventory_item_with_no_variants_raises(mock_shopify_client):
    mock_shopify_client.graphql.side_effect = [_inventory_item()]

    with pytest.raises(ValueError, match="Could not resolve product from inventory_item_id=555"):
        await build_product_metadata_from_shopify(inventory_item_id=555, client=mock_shopify_client)


@pytest.mark.asyncio
async def test_inventory_item_shared_across_products_refuses_to_guess(mock_shopify_client):
    mock_shopify_client.graphql.side_effect = [
        _inventory_item("gid://shopify/Product/111", "gid://shopify/Product/222"),
    ]

    with pytest.raises(ValueError) as exc:
        await build_product_metadata_from_shopify(inventory_item_id=555, client=mock_shopify_client)

    message = str(exc.value)
    assert "shared by variants of 2 products" in message
    assert "gid://shopify/Product/111" in message and "gid://shopify/Product/222" in message
    # The product fetch must not happen when attribution is ambiguous.
    assert mock_shopify_client.graphql.call_count == 1


@pytest.mark.asyncio
async def test_missing_inventory_item_raises(mock_shopify_client):
    mock_shopify_client.graphql.side_effect = [{"inventoryItem": None}]

    with pytest.raises(ValueError, match="Inventory item not found: 555"):
        await build_product_metadata_from_shopify(inventory_item_id=555, client=mock_shopify_client)


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
            "collections": {"nodes": []},
            "metafields": {"nodes": []},
            "variants": {"nodes": []}
        }
    }

    metadata = await build_product_metadata_from_shopify(product_id=123, client=mock_shopify_client)

    assert metadata.override_date_raw is None