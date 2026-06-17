"""
shopify_client.py  (preorder-service)   <-- drop in as preorder-service/shopify_client.py

Canonical async Shopify Admin GraphQL client. Token comes from the process-wide
AsyncTokenManager (client credentials grant) in shopify_token.py — this client
never reads SHOPIFY_ACCESS_TOKEN.

Handles, in one place: token injection + 401 refresh-and-retry, 5xx/network
backoff, and GraphQL THROTTLED backoff. This supersedes both the original
canonical client and the private ShopifyClient that lived in
ledger_reconciliation.py.

Everything that goes through this client is fixed automatically:
  - FastAPI routes via dependencies.get_shopify_client()
  - shopify_service.py
  - jobs/pub_date_transition.py
  - jobs/order_tagger.py and ledger_reconciliation.py (once converged onto it)

Env:
  SHOP_URL              required, e.g. castironbooks.myshopify.com
  SHOPIFY_API_VERSION   preferred; API_VERSION accepted for back-compat
  (auth handled by shopify_token: SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET)
"""

import os
import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from shopify_token import get_token_manager

logger = logging.getLogger(__name__)


class ShopifyGraphQLError(Exception):
    pass


class ShopifyHTTPError(Exception):
    pass


class ShopifyClient:
    def __init__(self) -> None:
        self.shop_url = os.getenv("SHOP_URL")
        if not self.shop_url:
            raise ValueError("SHOP_URL is not set")

        # This repo reads the version under two names (API_VERSION vs
        # SHOPIFY_API_VERSION). Accept both; standardize on SHOPIFY_API_VERSION.
        self.api_version = (
            os.getenv("SHOPIFY_API_VERSION")
            or os.getenv("API_VERSION")
            or "2025-10"
        )

        domain = self.shop_url.split("://", 1)[-1].rstrip("/")
        self.endpoint = f"https://{domain}/admin/api/{self.api_version}/graphql.json"

        self._tokens = get_token_manager()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=20.0),
            headers={"Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def graphql(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        max_retries: int = 5,
    ) -> Dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        attempt = 0
        backoff = 0.5
        did_auth_retry = False

        while True:
            attempt += 1
            self._client.headers["X-Shopify-Access-Token"] = await self._tokens.get_token()

            try:
                response = await self._client.post(self.endpoint, json=payload)
            except httpx.RequestError as exc:
                if attempt >= max_retries:
                    raise ShopifyHTTPError(f"Network error after {attempt} attempts: {exc}") from exc
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)
                continue

            # Expired/invalid token: refresh once, retry (does not consume the budget).
            if response.status_code == 401 and not did_auth_retry:
                logger.warning("[shopify] 401; refreshing token and retrying once.")
                did_auth_retry = True
                self._tokens.invalidate()
                self._client.headers["X-Shopify-Access-Token"] = \
                    await self._tokens.get_token(force_refresh=True)
                continue

            if response.status_code >= 500:
                if attempt >= max_retries:
                    raise ShopifyHTTPError(f"Shopify 5xx error: {response.status_code} {response.text}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)
                continue

            if response.status_code != 200:
                raise ShopifyHTTPError(f"Shopify HTTP error {response.status_code}: {response.text}")

            data = response.json()

            if "errors" in data:
                errors = data["errors"]
                throttled = isinstance(errors, list) and any(
                    isinstance(e, dict) and e.get("extensions", {}).get("code") == "THROTTLED"
                    for e in errors
                )
                if throttled and attempt < max_retries:
                    logger.warning("[shopify] THROTTLED; backing off %.1fs", backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10)
                    continue
                raise ShopifyGraphQLError(errors)

            return data.get("data", {})

    async def fetch_product_full(self, product_id: int) -> Dict[str, Any]:
        """Fetch canonical product state: tags, collections, metafields, inventory."""
        query = """
        query GetProduct($id: ID!) {
          product(id: $id) {
            id
            tags
            publishedAt
            status
            collections(first: 50) { edges { node { id handle title } } }
            metafields(first: 20) { edges { node { namespace key value } } }
            variants(first: 10) { edges { node { id inventoryQuantity inventoryPolicy } } }
          }
        }
        """
        gid = f"gid://shopify/Product/{product_id}"
        result = await self.graphql(query=query, variables={"id": gid})
        product = result.get("product")
        if not product:
            raise ShopifyGraphQLError(f"Product {product_id} not found")
        return product


def get_shopify_client() -> ShopifyClient:
    """Factory used by shopify_service and others. Uses env config."""
    return ShopifyClient()