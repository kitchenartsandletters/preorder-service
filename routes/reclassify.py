from fastapi import APIRouter, Depends, HTTPException
from typing import List
from pydantic import BaseModel
from dependencies import get_supabase_client, get_shopify_client, require_admin_key

import asyncio
import time
import logging

from schemas.reclassify import (
    ReclassifyResponse,
    BatchReclassifyResponse,
)
from services.reclassification_service import reclassify_single_product

router = APIRouter(prefix="/reclassify", tags=["Reclassification"])

class BatchRequest(BaseModel):
    product_ids: List[int]

@router.post("/batch", response_model=BatchReclassifyResponse)
async def reclassify_batch(
    request: BatchRequest,
    _admin=Depends(require_admin_key),
    supabase=Depends(get_supabase_client),
    shopify_client=Depends(get_shopify_client),
):

    results = []
    processed = 0

    for pid in request.product_ids:
        try:
            result = await reclassify_single_product(
                supabase=supabase,
                shopify_client=shopify_client,
                product_id=pid,
            )
            results.append(result)
            processed += 1
        except Exception:
            continue

    return BatchReclassifyResponse(
        total_requested=len(request.product_ids),
        total_processed=processed,
        results=results,
    )

@router.post("/active")
async def reclassify_active_products(
    _admin=Depends(require_admin_key),
    supabase=Depends(get_supabase_client),
    shopify_client=Depends(get_shopify_client),
):
    start_time = time.time()
    logger = logging.getLogger("uvicorn.error")

    products = supabase.schema("preorder").table("product_status") \
        .select("product_id") \
        .neq("status", "not_a_preorder_product") \
        .execute()

    product_ids = [row["product_id"] for row in products.data]

    logger.info(f"Starting parallel reclassification for {len(product_ids)} products")

    semaphore = asyncio.Semaphore(15)

    async def worker(pid: int):
        async with semaphore:
            try:
                result = await reclassify_single_product(
                    supabase=supabase,
                    shopify_client=shopify_client,
                    product_id=pid,
                )
                logger.info(f"Reclassified product {pid}")
                return {"product_id": pid, "status": "ok", "result": result}
            except Exception as e:
                logger.error(f"Reclassification failed for {pid}: {e}")
                return {"product_id": pid, "status": "error", "error": str(e)}

    tasks = [worker(pid) for pid in product_ids]

    results = await asyncio.gather(*tasks)

    success = [r for r in results if r["status"] == "ok"]
    failures = [r for r in results if r["status"] == "error"]

    runtime = round(time.time() - start_time, 2)

    logger.info(
        f"Reclassification finished: {len(success)} succeeded, {len(failures)} failed in {runtime}s"
    )

    return {
        "processed": len(results),
        "succeeded": len(success),
        "failed": len(failures),
        "runtime_seconds": runtime,
        "failures": failures,
    }

@router.post("/{product_id}")
async def reclassify_product(
    product_id: int,
    _admin=Depends(require_admin_key),
    supabase=Depends(get_supabase_client),
    shopify_client=Depends(get_shopify_client),
):
    try:
        return await reclassify_single_product(
            supabase=supabase,
            shopify_client=shopify_client,
            product_id=product_id,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))