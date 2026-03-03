# shopify_service.py
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from domain_models import ProductMetadata


DATE_TAG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


PRODUCT_FULL_QUERY = """
query ProductFull($id: ID!, $collectionsFirst: Int!, $variantsFirst: Int!, $metafieldsFirst: Int!) {
  product(id: $id) {
    id
    tags
    collections(first: $collectionsFirst) {
      nodes { handle }
    }
    variants(first: $variantsFirst) {
      nodes { inventoryQuantity }
    }
    metafields(first: $metafieldsFirst, namespace: "custom") {
      nodes { key value }
    }
  }
}
"""


def _split_tags(tags_field: Any) -> List[str]:
    """
    Shopify product.tags via REST webhooks usually arrives as a comma-separated string.
    GraphQL returns [String!] (list). We handle both.
    """
    if tags_field is None:
        return []

    # GraphQL: list[str]
    if isinstance(tags_field, list):
        return [str(t).strip() for t in tags_field if str(t).strip()]

    # REST/webhook: "a, b, c"
    if isinstance(tags_field, str):
        return [t.strip() for t in tags_field.split(",") if t.strip()]

    # Fallback: stringify
    return [str(tags_field).strip()] if str(tags_field).strip() else []


def _extract_date_tags_raw(tags: List[str]) -> List[str]:
    return [t for t in tags if DATE_TAG_RE.match(t)]


def _is_in_preorder_collection(collection_handles: List[str]) -> bool:
    """
    Determine preorder collection membership.
    Handles historical handle variations and normalizes case.
    """
    normalized = {h.strip().lower() for h in collection_handles if h}
    return any(
        handle in normalized
        for handle in {"preorder", "pre-order"}
    )


def _sum_inventory(variant_nodes: List[Dict[str, Any]]) -> int:
    total = 0
    for v in variant_nodes or []:
        qty = v.get("inventoryQuantity")
        try:
            total += int(qty)
        except Exception:
            # If Shopify returns null or weird, treat as 0
            total += 0
    return total


def _metafield_value(metafield_nodes: List[Dict[str, Any]], key: str) -> Optional[str]:
    for node in metafield_nodes or []:
        if node.get("key") == key:
            val = node.get("value")
            return None if val is None else str(val)
    return None


async def _graphql(client: Any, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compatibility shim: works with common client method names.
    Update the preferred branch first to match your shopify_client.py.
    """
    if hasattr(client, "graphql") and callable(getattr(client, "graphql")):
        return await client.graphql(query=query, variables=variables)
    if hasattr(client, "execute") and callable(getattr(client, "execute")):
        return await client.execute(query=query, variables=variables)
    if hasattr(client, "query") and callable(getattr(client, "query")):
        return await client.query(query=query, variables=variables)

    raise AttributeError(
        "Shopify client must expose an async method named graphql(query, variables) "
        "or execute(query, variables) or query(query, variables)."
    )


def _gid_for_product_id(product_id: int) -> str:
    return f"gid://shopify/Product/{int(product_id)}"


async def fetch_product_payload(
    client: Any,
    product_id: int,
    collections_first: int = 250,
    variants_first: int = 250,
    metafields_first: int = 50,
) -> Dict[str, Any]:
    """
    Fetch raw Shopify product payload via GraphQL.

    Returns the dict under data['product'].
    Raises if product is not found.
    """
    variables = {
        "id": _gid_for_product_id(product_id),
        "collectionsFirst": collections_first,
        "variantsFirst": variants_first,
        "metafieldsFirst": metafields_first,
    }

    resp = await _graphql(client, PRODUCT_FULL_QUERY, variables)

    # Support either {data:{...}} or direct {product:{...}}
    data = resp.get("data") if isinstance(resp, dict) else None
    product = None

    if isinstance(data, dict):
        product = data.get("product")
    elif isinstance(resp, dict):
        product = resp.get("product")

    if not product:
        raise ValueError(f"Shopify product not found for product_id={product_id}")

    return product


INVENTORY_ITEM_TO_PRODUCT_QUERY = """
query InventoryItemToProduct($id: ID!) {
  inventoryItem(id: $id) {
    variant {
      product {
        id
      }
    }
  }
}
"""


async def build_product_metadata_from_shopify(
    *,
    product_id: Optional[int] = None,
    inventory_item_id: Optional[int] = None,
    client: Any = None,
) -> ProductMetadata:
    """
    Shopify → ProductMetadata
    Supports:
    - product_id
    - inventory_item_id (resolves to product_id first)
    No persistence.
    No classification.
    No Supabase.
    """
    if client is None:
        # Lazily construct a Shopify client from environment configuration
        try:
            from shopify_client import get_shopify_client  # adjust if your factory name differs
            client = get_shopify_client()
        except Exception as e:
            raise ValueError(
                "client must be provided and could not be auto-created via get_shopify_client()"
            ) from e
    if product_id is None and inventory_item_id is None:
        raise ValueError("Either product_id or inventory_item_id must be provided")

    if product_id is None and inventory_item_id is not None:
        # Resolve product_id from inventory_item_id
        inventory_gid = f"gid://shopify/InventoryItem/{int(inventory_item_id)}"
        resp = await _graphql(
            client,
            INVENTORY_ITEM_TO_PRODUCT_QUERY,
            {"id": inventory_gid},
        )
        data = resp.get("data") if isinstance(resp, dict) else None
        inventory_item = None
        if isinstance(data, dict):
            inventory_item = data.get("inventoryItem")
        elif isinstance(resp, dict):
            inventory_item = resp.get("inventoryItem")
        if not inventory_item:
            raise ValueError(f"Inventory item not found: {inventory_item_id}")
        product_gid = (
            (inventory_item.get("variant") or {})
            .get("product", {})
            .get("id")
        )
        if not product_gid:
            raise ValueError(f"Could not resolve product from inventory_item_id={inventory_item_id}")
        product_id = int(product_gid.split("/")[-1])

    # Fetch full product
    product = await fetch_product_payload(client=client, product_id=product_id)
    collection_nodes = product.get("collections", {}).get("nodes", [])
    collection_handles = [
        str(c.get("handle")).strip()
        for c in collection_nodes
        if c.get("handle")
    ]
    in_preorder_collection = _is_in_preorder_collection(collection_handles)
    metafield_nodes = product.get("metafields", {}).get("nodes", [])
    override_date_raw = _metafield_value(
        metafield_nodes,
        "preorder_override_date",
    )
    pub_date_raw = _metafield_value(
        metafield_nodes,
        "pub_date",
    )

    print(product_id, collection_handles)

    return ProductMetadata(
        product_id=product_id,
        tags=_split_tags(product.get("tags")),
        in_preorder_collection=in_preorder_collection,
        override_date_raw=override_date_raw,
        pub_date_raw=pub_date_raw,
        date_tags_raw=_extract_date_tags_raw(_split_tags(product.get("tags"))),
        inventory=_sum_inventory(
            product.get("variants", {}).get("nodes", [])
        )
    )