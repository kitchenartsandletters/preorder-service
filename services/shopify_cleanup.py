"""
Shopify Cleanup Service
=======================
Handles product description sanitization, Preorder collection removal,
and Catch All channel unpublish operations.

All mutations use the current Shopify GraphQL Admin API patterns:
- productUpdate uses ProductUpdateInput (not deprecated ProductInput)
- publishableUnpublish uses publicationId (not channelId)

These operations were previously handled by preorderManager.py in the
NYT_weekly_and_preorder_release repo. This module replaces that
automation with on-demand, admin-controlled operations.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

PREORDER_COLLECTION_GID = "gid://shopify/Collection/262002344069"
CATCH_ALL_PUBLICATION_GID = "gid://shopify/Publication/103510278277"

# ──────────────────────────────────────────────
# Description analysis + sanitization
# ──────────────────────────────────────────────

PREAMBLE_MARKER = "this is a featured preorder"
PREAMBLE_MARKER_ALT = "this is a *featured preorder"

FOOTER_MARKER = "featured preorder books earn you an"


@dataclass
class DescriptionAnalysis:
    """Result of analyzing a product's Body HTML for preorder content."""

    has_preamble: bool
    has_footer: bool
    original_html: str
    cleaned_html: str

    @property
    def status(self) -> str:
        """
        Returns the Body HTML status label:
        - 'enriched'  = both preamble and footer present (standard preorder state)
        - 'partial'   = only one of preamble/footer present (anomalous)
        - 'cleaned'   = neither present (already sanitized)
        """
        if self.has_preamble and self.has_footer:
            return "enriched"
        elif self.has_preamble or self.has_footer:
            return "partial"
        else:
            return "cleaned"

    @property
    def needs_cleaning(self) -> bool:
        return self.has_preamble or self.has_footer


def analyze_description(description_html: str) -> DescriptionAnalysis:
    """
    Analyze a product's Body HTML to determine preorder content status.
    Does NOT modify Shopify — purely reads and classifies.
    """
    if not description_html:
        return DescriptionAnalysis(
            has_preamble=False,
            has_footer=False,
            original_html=description_html or "",
            cleaned_html=description_html or "",
        )

    lower = description_html.lower()
    has_preamble = PREAMBLE_MARKER in lower or PREAMBLE_MARKER_ALT in lower
    has_footer = FOOTER_MARKER in lower

    cleaned = _clean_description(description_html) if (has_preamble or has_footer) else description_html

    return DescriptionAnalysis(
        has_preamble=has_preamble,
        has_footer=has_footer,
        original_html=description_html,
        cleaned_html=cleaned,
    )


def _clean_description(html: str) -> str:
    """
    Remove preorder preamble and footer from Body HTML.

    Preamble to remove:
      1. The "This is a Featured Preorder*..." paragraph (pub date line)
      2. Any signing announcement paragraph between the preorder line
         and the publisher intro (e.g. "We're delighted that [author]
         will sign...")

    Preserved:
      - "This is what the publisher tells us about this book:" — this line
        is the transition into the actual product content and must be kept
        when present.

    Footer to remove:
      - Everything from "* Featured Preorder books earn you an extra"
        to the end of the description.
    """
    cleaned = html

    # Remove preamble paragraphs (preorder line + optional signing announcement)
    # Strategy: find the preamble marker, then remove paragraphs forward
    # until we hit either the publisher intro or non-preamble content.
    lower_cleaned = cleaned.lower()
    preamble_idx = lower_cleaned.find(PREAMBLE_MARKER)
    if preamble_idx == -1:
        preamble_idx = lower_cleaned.find(PREAMBLE_MARKER_ALT)
    if preamble_idx != -1:
        publisher_marker = "this is what the publisher tells us about this book:"
        publisher_idx = cleaned.lower().find(publisher_marker)

        if publisher_idx != -1:
            # Remove everything from the preamble <p> up to (but NOT including)
            # the publisher intro <p>
            preamble_block_start = cleaned.rfind("<p", 0, preamble_idx)
            if preamble_block_start == -1:
                preamble_block_start = preamble_idx

            # The publisher intro's containing <p> tag starts here
            publisher_block_start = cleaned.rfind("<p", 0, publisher_idx)
            if publisher_block_start == -1:
                publisher_block_start = publisher_idx

            cleaned = cleaned[:preamble_block_start] + cleaned[publisher_block_start:].lstrip()
        else:
            # No publisher intro — just remove the single preamble paragraph
            end_of_preamble = cleaned.find("</p>", preamble_idx)
            if end_of_preamble != -1:
                preamble_block_start = cleaned.rfind("<p", 0, preamble_idx)
                if preamble_block_start == -1:
                    preamble_block_start = preamble_idx
                cleaned = cleaned[:preamble_block_start] + cleaned[end_of_preamble + len("</p>"):].lstrip()

    # Remove signing announcement if it sits between preamble removal point
    # and publisher intro. After preamble removal above, a signing line may
    # now be at the start. Check for common signing patterns.
    # (This is already handled by the block removal above when publisher_idx
    # is present, since we cut everything between preamble start and publisher start.)

    # Remove footer
    footer_idx = cleaned.lower().find(FOOTER_MARKER)
    if footer_idx != -1:
        # Find the start of the footer's containing <p> or <span> tag
        footer_block_start = cleaned.rfind("<p", 0, footer_idx)
        if footer_block_start == -1:
            footer_block_start = cleaned.rfind("<span", 0, footer_idx)
        if footer_block_start == -1:
            footer_block_start = footer_idx
        cleaned = cleaned[:footer_block_start].rstrip()

    return cleaned.strip()


# ──────────────────────────────────────────────
# GraphQL queries and mutations
# ──────────────────────────────────────────────

PRODUCT_CLEANUP_STATE_QUERY = """
query ProductCleanupState($id: ID!) {
  product(id: $id) {
    id
    title
    descriptionHtml
    tags
    collections(first: 10) {
      edges {
        node {
          id
          handle
          title
        }
      }
    }
    variants(first: 1) {
      edges {
        node {
          inventoryQuantity
          barcode
        }
      }
    }
    metafields(first: 10, namespace: "custom") {
      edges {
        node {
          key
          value
        }
      }
    }
    resourcePublicationsV2(first: 10) {
      edges {
        node {
          publication {
            id
            name
          }
          isPublished
        }
      }
    }
  }
}
"""

UPDATE_DESCRIPTION_MUTATION = """
mutation UpdateProductDescription($product: ProductUpdateInput!) {
  productUpdate(product: $product) {
    product {
      id
      descriptionHtml
    }
    userErrors {
      field
      message
    }
  }
}
"""

COLLECTION_REMOVE_MUTATION = """
mutation collectionRemoveProducts($id: ID!, $productIds: [ID!]!) {
  collectionRemoveProducts(id: $id, productIds: $productIds) {
    userErrors {
      field
      message
    }
  }
}
"""

UNPUBLISH_MUTATION = """
mutation publishableUnpublish($id: ID!, $input: [PublicationInput!]!) {
  publishableUnpublish(id: $id, input: $input) {
    userErrors {
      field
      message
    }
  }
}
"""

PUBLISH_MUTATION = """
mutation publishablePublish($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    userErrors {
      field
      message
    }
  }
}
"""


# ──────────────────────────────────────────────
# Shopify operations
# ──────────────────────────────────────────────

@dataclass
class CleanupState:
    """Full cleanup state for a product, as fetched from Shopify."""

    product_gid: str
    product_id: int
    title: str
    description_html: str
    description_analysis: DescriptionAnalysis
    tags: List[str]
    inventory: int
    barcode: Optional[str]
    pub_date: Optional[str]
    in_preorder_collection: bool
    preorder_collection_gid: Optional[str]
    published_to_catch_all: bool
    publications: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_gid": self.product_gid,
            "title": self.title,
            "description_status": self.description_analysis.status,
            "description_needs_cleaning": self.description_analysis.needs_cleaning,
            "has_preamble": self.description_analysis.has_preamble,
            "has_footer": self.description_analysis.has_footer,
            "tags": self.tags,
            "inventory": self.inventory,
            "barcode": self.barcode,
            "pub_date": self.pub_date,
            "in_preorder_collection": self.in_preorder_collection,
            "published_to_catch_all": self.published_to_catch_all,
        }


def _extract_product_id(gid: str) -> int:
    return int(gid.split("/")[-1])


def _metafield_value(edges: List[Dict], key: str) -> Optional[str]:
    for edge in edges:
        node = edge.get("node", {})
        if node.get("key") == key:
            return node.get("value")
    return None


async def fetch_cleanup_state(client: Any, product_id: int) -> CleanupState:
    """
    Fetch the full cleanup state for a single product from Shopify.
    Returns a CleanupState with description analysis, collection membership,
    and publication channel status.
    """
    gid = f"gid://shopify/Product/{product_id}"
    result = await client.graphql(
        query=PRODUCT_CLEANUP_STATE_QUERY,
        variables={"id": gid},
    )

    product = result.get("product")
    if not product:
        raise ValueError(f"Product not found: {product_id}")

    description_html = product.get("descriptionHtml") or ""
    analysis = analyze_description(description_html)

    # Collections
    collection_edges = product.get("collections", {}).get("edges", [])
    in_preorder = False
    preorder_gid = None
    for edge in collection_edges:
        node = edge["node"]
        if node["handle"] in ("pre-order", "preorder"):
            in_preorder = True
            preorder_gid = node["id"]
            break

    # Publications
    pub_edges = product.get("resourcePublicationsV2", {}).get("edges", [])
    published_to_catch_all = False
    publications = []
    for edge in pub_edges:
        node = edge["node"]
        pub = node.get("publication", {})
        publications.append({
            "id": pub.get("id"),
            "name": pub.get("name"),
            "is_published": node.get("isPublished"),
        })
        if pub.get("id") == CATCH_ALL_PUBLICATION_GID and node.get("isPublished"):
            published_to_catch_all = True

    # Variants
    variant_edges = product.get("variants", {}).get("edges", [])
    inventory = 0
    barcode = None
    if variant_edges:
        variant = variant_edges[0]["node"]
        inventory = variant.get("inventoryQuantity", 0)
        barcode = variant.get("barcode")

    # Metafields
    metafield_edges = product.get("metafields", {}).get("edges", [])
    pub_date = _metafield_value(metafield_edges, "pub_date")

    tags = product.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    return CleanupState(
        product_gid=product["id"],
        product_id=_extract_product_id(product["id"]),
        title=product.get("title", ""),
        description_html=description_html,
        description_analysis=analysis,
        tags=tags,
        inventory=inventory,
        barcode=barcode,
        pub_date=pub_date,
        in_preorder_collection=in_preorder,
        preorder_collection_gid=preorder_gid,
        published_to_catch_all=published_to_catch_all,
        publications=publications,
    )


async def clean_description(client: Any, product_id: int, cleaned_html: str) -> List[Dict]:
    """
    Update a product's Body HTML to the cleaned version.
    Returns list of userErrors (empty on success).
    """
    gid = f"gid://shopify/Product/{product_id}"
    result = await client.graphql(
        query=UPDATE_DESCRIPTION_MUTATION,
        variables={
            "product": {
                "id": gid,
                "descriptionHtml": cleaned_html,
            }
        },
    )
    errors = result.get("productUpdate", {}).get("userErrors", [])
    if errors:
        logger.error(f"Failed to update description for {product_id}: {errors}")
    else:
        logger.info(f"Description cleaned for product {product_id}")
    return errors


async def remove_from_preorder_collection(client: Any, product_id: int) -> List[Dict]:
    """
    Remove a product from the Preorder collection.
    Returns list of userErrors (empty on success).
    """
    gid = f"gid://shopify/Product/{product_id}"
    result = await client.graphql(
        query=COLLECTION_REMOVE_MUTATION,
        variables={
            "id": PREORDER_COLLECTION_GID,
            "productIds": [gid],
        },
    )
    errors = result.get("collectionRemoveProducts", {}).get("userErrors", [])
    if errors:
        logger.error(f"Failed to remove {product_id} from Preorder collection: {errors}")
    else:
        logger.info(f"Removed product {product_id} from Preorder collection")
    return errors


async def unpublish_from_catch_all(client: Any, product_id: int) -> List[Dict]:
    """
    Unpublish a product from the Catch All publication channel.
    Returns list of userErrors (empty on success).
    """
    gid = f"gid://shopify/Product/{product_id}"
    result = await client.graphql(
        query=UNPUBLISH_MUTATION,
        variables={
            "id": gid,
            "input": [{"publicationId": CATCH_ALL_PUBLICATION_GID}],
        },
    )
    errors = result.get("publishableUnpublish", {}).get("userErrors", [])
    if errors:
        logger.error(f"Failed to unpublish {product_id} from Catch All: {errors}")
    else:
        logger.info(f"Unpublished product {product_id} from Catch All")
    return errors


async def publish_to_catch_all(client: Any, product_id: int) -> List[Dict]:
    """
    Republish a product to the Catch All publication channel.
    Used for error recovery.
    Returns list of userErrors (empty on success).
    """
    gid = f"gid://shopify/Product/{product_id}"
    result = await client.graphql(
        query=PUBLISH_MUTATION,
        variables={
            "id": gid,
            "input": [{"publicationId": CATCH_ALL_PUBLICATION_GID}],
        },
    )
    errors = result.get("publishablePublish", {}).get("userErrors", [])
    if errors:
        logger.error(f"Failed to republish {product_id} to Catch All: {errors}")
    else:
        logger.info(f"Republished product {product_id} to Catch All")
    return errors