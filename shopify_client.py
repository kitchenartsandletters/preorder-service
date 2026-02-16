

import os
import asyncio
from typing import Any, Dict, Optional

import httpx


class ShopifyGraphQLError(Exception):
    pass


class ShopifyHTTPError(Exception):
    pass


class ShopifyClient:
    def __init__(self) -> None:
        self.shop_url = os.getenv("SHOP_URL")
        self.access_token = os.getenv("SHOPIFY_ACCESS_TOKEN")
        self.api_version = os.getenv("API_VERSION", "2025-10")

        if not self.shop_url:
            raise ValueError("SHOP_URL is not set")
        if not self.access_token:
            raise ValueError("SHOPIFY_ACCESS_TOKEN is not set")

        self.endpoint = (
            f"https://{self.shop_url}/admin/api/{self.api_version}/graphql.json"
        )

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={
                "X-Shopify-Access-Token": self.access_token,
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def graphql(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        payload = {
            "query": query,
            "variables": variables or {},
        }

        attempt = 0
        backoff = 0.5

        while True:
            attempt += 1

            try:
                response = await self._client.post(self.endpoint, json=payload)
            except httpx.RequestError as exc:
                if attempt >= max_retries:
                    raise ShopifyHTTPError(
                        f"Network error after {attempt} attempts: {exc}"
                    ) from exc
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            if response.status_code >= 500:
                if attempt >= max_retries:
                    raise ShopifyHTTPError(
                        f"Shopify 5xx error: {response.status_code} {response.text}"
                    )
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            if response.status_code != 200:
                raise ShopifyHTTPError(
                    f"Shopify HTTP error {response.status_code}: {response.text}"
                )

            data = response.json()

            if "errors" in data:
                raise ShopifyGraphQLError(data["errors"])

            return data.get("data", {})

    async def fetch_product_full(self, product_id: int) -> Dict[str, Any]:
        """
        Fetch canonical product state including:
        - tags
        - collections
        - metafields (including custom.preorder_override_date)
        - inventory
        """

        query = """
        query GetProduct($id: ID!) {
          product(id: $id) {
            id
            tags
            publishedAt
            status
            collections(first: 50) {
              edges {
                node {
                  id
                  handle
                  title
                }
              }
            }
            metafields(first: 20) {
              edges {
                node {
                  namespace
                  key
                  value
                }
              }
            }
            variants(first: 10) {
              edges {
                node {
                  id
                  inventoryQuantity
                  inventoryPolicy
                }
              }
            }
          }
        }
        """

        gid = f"gid://shopify/Product/{product_id}"

        result = await self.graphql(
            query=query,
            variables={"id": gid},
        )

        product = result.get("product")
        if not product:
            raise ShopifyGraphQLError(f"Product {product_id} not found")

        return product