from typing import List
from shopify_service import build_product_metadata_from_shopify
from orchestrator import classify_and_persist_product
from schemas.reclassify import ReclassifyResponse
from domain_models import ProductMetadata


async def reclassify_single_product(
    *,
    supabase,
    shopify_client,
    product_id: int,
    engine_version: str = "v1",
) -> ReclassifyResponse:

    metadata: ProductMetadata = await build_product_metadata_from_shopify(
        product_id=product_id,
        client=shopify_client,
    )

    result = classify_and_persist_product(
        supabase=supabase,
        product_metadata=metadata,
        engine_version=engine_version,
    )

    return ReclassifyResponse(
        product_id=metadata.product_id,
        status=result.status,
        anomaly_type=result.anomaly_type,
        effective_pub_date=(
            result.effective_pub_date.isoformat()
            if result.effective_pub_date
            else None
        ),
        engine_version=engine_version,
    )