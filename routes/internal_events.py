from fastapi import APIRouter, Depends, HTTPException, Header
import os
import time
import logging

from dependencies import get_supabase_client, get_shopify_client
from services.reclassification_service import reclassify_single_product
from shopify_service import build_product_metadata_from_shopify
from orchestrator import classify_and_persist_product

router = APIRouter(prefix="/internal", tags=["Internal Events"])

logger = logging.getLogger("uvicorn.error")

ADMIN_TOKEN = os.getenv("PREORDER_ADMIN_TOKEN")
RECENT_EVENTS = {}
DEDUP_WINDOW_SECONDS = 5


def verify_internal_key(x_admin_key: str | None):
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="PREORDER_ADMIN_TOKEN not configured")

    if x_admin_key != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/event")
async def internal_event(
    payload: dict,
    x_admin_key: str | None = Header(default=None),
    supabase=Depends(get_supabase_client),
    shopify_client=Depends(get_shopify_client),
):

    verify_internal_key(x_admin_key)

    start = time.time()

    event_type = payload.get("type")

    event_id = payload.get("event_id")

    if not event_id:
        raise HTTPException(status_code=422, detail="event_id required")

    # --- Persistent idempotency check ---
    existing = (
        supabase.table("processed_events")
        .select("event_id")
        .eq("event_id", event_id)
        .execute()
    )

    if existing.data:
        logger.info(f"Skipped already processed event_id={event_id}")
        return {"ok": True, "skipped": "already_processed"}

    # --- Dedupe guard ---
    now = time.time()

    dedupe_key = event_id

    last_seen = RECENT_EVENTS.get(dedupe_key)

    if last_seen and now - last_seen < DEDUP_WINDOW_SECONDS:
        logger.info(f"Deduped event {dedupe_key}")
        return {"ok": True, "skipped": "deduped"}

    RECENT_EVENTS[dedupe_key] = now

    try:

        if event_type == "product.updated":

            product_id = payload.get("product_id")

            if not product_id:
                raise HTTPException(status_code=422, detail="product_id required")

            result = await reclassify_single_product(
                supabase=supabase,
                shopify_client=shopify_client,
                product_id=product_id,
            )

        elif event_type == "inventory.updated":

            inventory_item_id = payload.get("inventory_item_id")

            if not inventory_item_id:
                raise HTTPException(status_code=422, detail="inventory_item_id required")

            # Build metadata from Shopify using inventory_item_id
            metadata = await build_product_metadata_from_shopify(
                inventory_item_id=inventory_item_id,
                client=shopify_client,
            )

            # Run classification + persistence
            result = classify_and_persist_product(
                supabase=supabase,
                product_metadata=metadata,
                engine_version="v1",
            )

        else:
            raise HTTPException(status_code=422, detail=f"Unknown event type {event_type}")

    except Exception as e:

        logger.exception("internal_event_failed")

        raise HTTPException(status_code=500, detail=str(e))

    # --- Persist processed event ---
    try:
        supabase.table("processed_events").insert(
            {
                "event_id": event_id,
                "event_type": event_type,
                "processed_at": int(time.time()),
            }
        ).execute()
    except Exception:
        logger.warning(f"Failed to persist event_id={event_id}")

    runtime = round(time.time() - start, 3)

    logger.info(
        f"Internal event processed type={event_type} runtime={runtime}s"
    )

    return {
        "ok": True,
        "runtime": runtime,
        "result": result,
    }