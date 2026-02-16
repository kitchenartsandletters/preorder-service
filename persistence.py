from datetime import datetime, UTC
from typing import Optional, Dict, Any

from classification.engine import ClassificationResult

ENGINE_VERSION = "v0.7-supabase-upsert"


def persist_classification(
    supabase,
    product_id: int,
    classification: ClassificationResult,
    metadata_snapshot: Optional[Dict[str, Any]] = None,
    engine_version: str = ENGINE_VERSION,
) -> None:
    """
    Always upsert classification result into preorder.product_status.

    This function:
    - Does not mutate classification
    - Does not read from database
    - Always performs an idempotent upsert
    """

    payload = {
        "product_id": product_id,
        "status": classification.status,
        "anomaly_type": classification.anomaly_type,
        "effective_pub_date": classification.effective_pub_date,
        "last_classified_at": datetime.now(UTC).isoformat(),
        "metadata_snapshot": metadata_snapshot,
        "engine_version": engine_version,
    }

    (
        supabase
        .table("preorder.product_status")
        .upsert(payload, on_conflict="product_id")
        .execute()
    )