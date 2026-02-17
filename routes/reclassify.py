from fastapi import APIRouter, Depends, HTTPException
from typing import List
from pydantic import BaseModel
from dependencies import get_supabase_client, get_shopify_client, require_admin_key

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