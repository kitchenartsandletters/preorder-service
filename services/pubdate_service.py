"""
services/pubdate_service.py

Reads and writes the `custom.pub_date` metafield — the single source of truth
for a preorder's date (rev2 decision D1).

Two things this module deliberately does NOT do:

1. **Hard-code the metafield type.** The type is read from the live metafield,
   or from its definition when the product has no value yet. Writing a wrong
   type creates a second metafield of the same namespace/key in some setups and
   silently diverges from what the storefront reads.

2. **Overwrite blindly.** Every write carries `compareDigest`, Shopify's
   compare-and-set for metafields (Admin API 2024-07+). The digest is obtained
   by querying the metafield's `compareDigest` field; if the metafield does not
   exist yet, `compareDigest` is explicitly null, which Shopify documents as the
   safe form of "create only if still absent". A mismatch means somebody edited
   the product between our read and our write, and the mutation fails rather
   than clobbering them — surfaced as 409 by the route.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

NAMESPACE = "custom"
KEY = "pub_date"

PUB_DATE_STATE_QUERY = """
query PubDateState($id: ID!, $namespace: String!, $key: String!) {
  product(id: $id) {
    id
    title
    metafield(namespace: $namespace, key: $key) {
      id
      type
      value
      compareDigest
    }
  }
  metafieldDefinitions(first: 1, ownerType: PRODUCT, namespace: $namespace, key: $key) {
    nodes { type { name } }
  }
}
"""

SET_PUB_DATE_MUTATION = """
mutation SetPubDate($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id namespace key value type compareDigest }
    userErrors { field message code }
  }
}
"""


class PubDateConflict(Exception):
    """The stored value changed between our read and our write."""


class PubDateError(Exception):
    """Shopify refused the write, or the product/metafield could not be read."""


def _gid(product_id: int) -> str:
    return f"gid://shopify/Product/{int(product_id)}"


async def read_pub_date_state(client, product_id: int) -> Dict[str, Any]:
    """
    Current pub_date, its type, and the compare-and-set digest.

    `metafield_type` falls back to the definition's type when the product has no
    value yet, so the first write for a product still uses the right type.
    """
    data = await client.graphql(
        query=PUB_DATE_STATE_QUERY,
        variables={"id": _gid(product_id), "namespace": NAMESPACE, "key": KEY},
    )
    product = (data or {}).get("product")
    if not product:
        raise PubDateError(f"Product {product_id} not found in Shopify")

    metafield = product.get("metafield") or {}
    definition_nodes = ((data or {}).get("metafieldDefinitions") or {}).get("nodes") or []
    definition_type = (definition_nodes[0].get("type") or {}).get("name") if definition_nodes else None

    return {
        "product_id": product_id,
        "title": product.get("title"),
        "value": metafield.get("value"),
        "metafield_type": metafield.get("type") or definition_type,
        "compare_digest": metafield.get("compareDigest"),
        "exists": bool(metafield.get("id")),
    }


async def set_pub_date(
    client,
    product_id: int,
    new_pub_date: date,
    metafield_type: str,
    compare_digest: Optional[str],
) -> Dict[str, Any]:
    """
    Write pub_date with compare-and-set.

    `compare_digest` must be the digest just read, or None when the metafield
    does not exist. Passing it is what makes a concurrent edit fail loudly.
    """
    if not metafield_type:
        raise PubDateError(
            "Could not determine the type of custom.pub_date from the metafield "
            "or its definition; refusing to guess."
        )

    variables = {
        "metafields": [{
            "ownerId": _gid(product_id),
            "namespace": NAMESPACE,
            "key": KEY,
            "type": metafield_type,
            "value": new_pub_date.isoformat(),
            "compareDigest": compare_digest,
        }]
    }
    data = await client.graphql(query=SET_PUB_DATE_MUTATION, variables=variables)
    payload = (data or {}).get("metafieldsSet") or {}
    errors = payload.get("userErrors") or []

    if errors:
        joined = "; ".join(
            f"{e.get('code') or ''} {e.get('message') or ''}".strip() for e in errors
        )
        # Shopify reports a digest mismatch as a userError on the mutation.
        if any("digest" in (e.get("message") or "").lower() for e in errors):
            raise PubDateConflict(
                f"custom.pub_date changed in Shopify since it was read: {joined}"
            )
        raise PubDateError(f"Shopify refused the pub_date write: {joined}")

    written = (payload.get("metafields") or [{}])[0]
    logger.info(
        f"[pubdate] product_id={product_id} pub_date set to {new_pub_date.isoformat()}"
    )
    return written
