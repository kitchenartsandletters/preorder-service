from fastapi import APIRouter, Depends, HTTPException, Header
import os
import time
import logging

from dependencies import get_supabase_client, get_shopify_client
from services.reclassification_service import reclassify_single_product

router = APIRouter(prefix="/internal", tags=["Internal Events"])

logger = logging.getLogger("uvicorn.error")

ADMIN_TOKEN = os.getenv("PREORDER_ADMIN_TOKEN")


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

        else:
            raise HTTPException(status_code=422, detail=f"Unknown event type {event_type}")

    except Exception as e:

        logger.exception("internal_event_failed")

        raise HTTPException(status_code=500, detail=str(e))

    runtime = round(time.time() - start, 3)

    logger.info(
        f"Internal event processed type={event_type} runtime={runtime}s"
    )

    return {
        "ok": True,
        "runtime": runtime,
        "result": result,
    }