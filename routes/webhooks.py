from fastapi import APIRouter, Request, Response, Header
import os, hmac, hashlib, base64, json
from typing import Optional
from ..db.connection import get_pool

router = APIRouter(prefix="/webhooks")

SHOPIFY_API_SECRET  = os.getenv("SHOPIFY_API_SECRET", "")
GATEWAY_HMAC_SECRET = os.getenv("GATEWAY_HMAC_SECRET", "")  # == gateway EXTERNAL_HMAC_SECRET

def _verify(raw: bytes, header_sig: Optional[str], secret: str) -> bool:
    if not header_sig or not secret:
        return False
    dig = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(dig).decode(), header_sig)

async def _ingest(request: Request,
                  x_shopify_hmac_sha256: Optional[str],
                  x_shopify_topic: Optional[str],
                  x_shopify_shop_domain: Optional[str],
                  x_gateway_signature: Optional[str],
                  x_gateway_event_id: Optional[str]) -> tuple[dict, dict]:
    raw = await request.body()

    # 1) Verify Shopify first (header from original request is preserved by gateway)
    ok = _verify(raw, x_shopify_hmac_sha256, SHOPIFY_API_SECRET)
    # 2) Fallback: verify gateway signature
    if not ok:
        ok = _verify(raw, x_gateway_signature, GATEWAY_HMAC_SECRET)
    if not ok:
        raise Response(status_code=401)

    payload = json.loads(raw.decode("utf-8"))
    headers = {
        "X-Shopify-Topic": x_shopify_topic,
        "X-Shopify-Shop-Domain": x_shopify_shop_domain,
        "X-Shopify-Hmac-Sha256": x_shopify_hmac_sha256,
        "X-Gateway-Signature": x_gateway_signature,
        "X-Gateway-Event-ID": x_gateway_event_id,
    }
    return payload, headers

async def _insert_tracking(pool, row: dict):
    await pool.execute("""
        insert into preorder.tracking
        (event_id, external_delivery_id, topic, shop_domain, source_service, order_id, order_name,
         customer_id, line_item_id, product_id, variant_id, sku, quantity, pub_date, status,
         approved, payload, headers, processed, processing_notes)
        values ($1,        null,               $2,    $3,         'webhook-gateway', $4,      $5,
                $6,         $7,          $8,        $9,       $10, $11,     $12,      $13,
                false,   $14,    $15,    false,    null)
        on conflict (event_id) do nothing
    """,
    row.get("event_id"), row.get("topic"), row.get("shop_domain"),
    row.get("order_id"), row.get("order_name"), row.get("customer_id"),
    row.get("line_item_id"), row.get("product_id"), row.get("variant_id"),
    row.get("sku"), row.get("quantity"), row.get("pub_date"),
    row.get("status"), json.dumps(row.get("payload")), json.dumps(row.get("headers"))
    )

def _extract_order_facts(topic: str, payload: dict) -> list[dict]:
    # produce rows (one per line item) with minimal extracted facts
    rows = []
    if topic in ("orders/create", "orders/updated", "orders/cancelled"):
        for li in payload.get("line_items", []):
            rows.append({
                "order_id": payload.get("id"),
                "order_name": payload.get("name"),
                "customer_id": (payload.get("customer") or {}).get("id"),
                "line_item_id": li.get("id"),
                "product_id": li.get("product_id"),
                "variant_id": li.get("variant_id"),
                "sku": li.get("sku"),
                "quantity": li.get("quantity"),
                "pub_date": None,     # fill later if you parse/store pub date elsewhere
                "status": "presale" if topic == "orders/create" else "pending"
            })
    elif topic == "products/update":
        rows.append({"status": "pending"})
    elif topic == "inventory_levels/update":
        rows.append({"status": "pending"})
    return rows or [{"status": "pending"}]

async def _handle(topic: str, request: Request,
                  x_shopify_hmac_sha256: Optional[str] = Header(default=None),
                  x_shopify_topic: Optional[str] = Header(default=None),
                  x_shopify_shop_domain: Optional[str] = Header(default=None),
                  x_gateway_signature: Optional[str] = Header(default=None),
                  x_gateway_event_id: Optional[str] = Header(default=None)):
    payload, headers = await _ingest(request, x_shopify_hmac_sha256, x_shopify_topic,
                                     x_shopify_shop_domain, x_gateway_signature, x_gateway_event_id)
    pool = await get_pool()
    facts_list = _extract_order_facts(topic, payload)
    for facts in facts_list:
        row = {
            "event_id": x_gateway_event_id,
            "topic": x_shopify_topic or topic,
            "shop_domain": x_shopify_shop_domain,
            "order_id": facts.get("order_id"),
            "order_name": facts.get("order_name"),
            "customer_id": facts.get("customer_id"),
            "line_item_id": facts.get("line_item_id"),
            "product_id": facts.get("product_id"),
            "variant_id": facts.get("variant_id"),
            "sku": facts.get("sku"),
            "quantity": facts.get("quantity"),
            "pub_date": facts.get("pub_date"),
            "status": facts.get("status", "pending"),
            "payload": payload,
            "headers": headers
        }
        await _insert_tracking(pool, row)
    return Response(status_code=200)

@router.post("/orders/create")
async def orders_create(request: Request, **kw):  return await _handle("orders/create", request, **kw)

@router.post("/orders/updated")
async def orders_updated(request: Request, **kw): return await _handle("orders/updated", request, **kw)

@router.post("/orders/cancelled")
async def orders_cancelled(request: Request, **kw): return await _handle("orders/cancelled", request, **kw)

@router.post("/products/update")
async def products_update(request: Request, **kw): return await _handle("products/update", request, **kw)

@router.post("/inventory-levels")
async def inventory_levels(request: Request, **kw): return await _handle("inventory_levels/update", request, **kw)