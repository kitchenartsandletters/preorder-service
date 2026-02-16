# reclassification.py

from typing import List, Dict, Any
from datetime import datetime, timezone

from orchestrator import classify_and_persist_product
from domain_models import ProductMetadata


def reclassify_single_product(
    supabase,
    product_metadata: ProductMetadata,
    engine_version: str,
) -> Dict[str, Any]:
    """
    Reclassify one product and persist result.

    Returns structured summary dictionary.
    """

    result = classify_and_persist_product(
        supabase=supabase,
        product_metadata=product_metadata,
        engine_version=engine_version,
    )

    return {
        "product_id": product_metadata.product_id,
        "status": result.status,
        "anomaly_type": result.anomaly_type,
        "effective_pub_date": result.effective_pub_date,
        "engine_version": engine_version,
        "reclassified_at": datetime.now(timezone.utc).isoformat(),
    }


def reclassify_batch(
    supabase,
    products: List[ProductMetadata],
    engine_version: str,
) -> Dict[str, Any]:
    """
    Reclassify multiple products.

    Continues processing even if some fail.
    Returns structured summary.
    """

    successes = []
    failures = []

    for product in products:
        try:
            result = reclassify_single_product(
                supabase=supabase,
                product_metadata=product,
                engine_version=engine_version,
            )
            successes.append(result)
        except Exception as e:
            failures.append(
                {
                    "product_id": product.product_id,
                    "error": str(e),
                }
            )

    return {
        "total": len(products),
        "success_count": len(successes),
        "failure_count": len(failures),
        "successes": successes,
        "failures": failures,
    }