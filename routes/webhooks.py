from fastapi import APIRouter, Request, Response, Header, HTTPException
from fastapi.responses import JSONResponse
import os, hmac, hashlib, base64, json
from typing import Optional
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.connection import get_pool
import uuid

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

    # Normalize all headers first
    x_shopify_hmac_sha256 = str(x_shopify_hmac_sha256 or "")
    x_shopify_topic = str(x_shopify_topic or "")
    x_shopify_shop_domain = str(x_shopify_shop_domain or "")
    x_gateway_signature = str(x_gateway_signature or "")
    x_gateway_event_id = str(x_gateway_event_id or "")

    # DEBUG: print for comparison
    print("DEBUG x_gateway_signature:", x_gateway_signature)
    print("DEBUG computed:", base64.b64encode(hmac.new(GATEWAY_HMAC_SECRET.encode(), raw, hashlib.sha256).digest()).decode())

    # 1) Verify Shopify first (usually absent)
    ok = _verify(raw, x_shopify_hmac_sha256, SHOPIFY_API_SECRET)
    # 2) Fallback: verify gateway signature
    if not ok:
        ok = _verify(raw, x_gateway_signature, GATEWAY_HMAC_SECRET)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")

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
        insert into preorder.tracking (
            event_id, topic, shop_domain, source_service,
            order_id, order_name, customer_id, line_item_id,
            product_id, variant_id, sku, quantity, pub_date,
            status, approved, payload, headers, processed, processing_notes
        )
        values (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
    """, (
        row.get("event_id"),
        row.get("topic"),
        row.get("shop_domain"),
        "gateway",  # source_service
        row.get("order_id"),
        row.get("order_name"),
        row.get("customer_id"),
        row.get("line_item_id"),
        row.get("product_id"),
        row.get("variant_id"),
        row.get("sku"),
        row.get("quantity"),
        row.get("pub_date"),
        row.get("status"),
        False,  # approved
        json.dumps(row.get("payload")),
        json.dumps(row.get("headers")),
        False,  # processed
        None    # processing_notes
    ))

    print(f"✅ Inserted preorder.tracking row for event_id={row.get('event_id')}")
    print("✅ Insert attempted; committing…")
    await pool.commit()
    print(f"✅ Commit complete for event_id={row.get('event_id')}")

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

async def _handle(topic: str, request: Request):
    # extract actual header strings here
    x_shopify_hmac_sha256 = request.headers.get("X-Shopify-Hmac-Sha256", "")
    x_shopify_topic = request.headers.get("X-Shopify-Topic", "")
    x_shopify_shop_domain = request.headers.get("X-Shopify-Shop-Domain", "")
    x_gateway_signature = request.headers.get("X-Gateway-Signature", "")
    x_gateway_event_id = request.headers.get("X-Gateway-Event-ID", "")

    payload, headers = await _ingest(
        request,
        x_shopify_hmac_sha256,
        x_shopify_topic,
        x_shopify_shop_domain,
        x_gateway_signature,
        x_gateway_event_id
    )
    pool = await get_pool()
    facts_list = _extract_order_facts(topic, payload)
    for facts in facts_list:
        row = {
            "event_id": x_gateway_event_id or str(uuid.uuid4()),
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
    return JSONResponse({"status": "ok"})

@router.post("/")
async def catch_all_root(request: Request):
    """
    Accepts POSTs sent to /webhooks with topic inferred from X-Shopify-Topic.
    Gateway will POST to PREORDER_WEBHOOK_URL (ending with /webhooks) and include
    X-Shopify-Topic so we can route appropriately.
    """
    topic = request.headers.get("X-Shopify-Topic", "")
    if not topic:
        raise HTTPException(status_code=400, detail="Missing X-Shopify-Topic header")

    # Normalize slashes — gateway may send topics like "products/update"
    normalized = topic.strip().lower()

    # Dispatch to internal handler
    return await _handle(normalized, request)

@router.post("/orders/create")
async def orders_create(request: Request):
    return await _handle("orders/create", request)

@router.post("/orders/updated")
async def orders_updated(request: Request):
    return await _handle("orders/updated", request)

@router.post("/orders/cancelled")
async def orders_cancelled(request: Request):
    return await _handle("orders/cancelled", request)

@router.post("/products/update")
async def products_update(request: Request):
    return await _handle("products/update", request)

@router.post("/inventory-levels")
async def inventory_levels(request: Request):
    return await _handle("inventory_levels/update", request)