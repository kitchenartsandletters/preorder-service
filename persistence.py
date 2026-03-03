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

    effective_pub_date = (
        classification.effective_pub_date.isoformat()
        if classification.effective_pub_date
        else None
    )

    payload = {
        "product_id": product_id,
        "status": classification.status,
        "anomaly_type": classification.anomaly_type,
        "effective_pub_date": effective_pub_date,
        "last_classified_at": datetime.now(UTC).isoformat(),
        "metadata_snapshot": metadata_snapshot,
        "engine_version": engine_version,
    }

    (
        supabase
        .schema("preorder")
        .table("product_status")
        .upsert(payload, on_conflict="product_id")
        .execute()
    )

def persist_inventory_arrival(
    supabase,
    product_id: int,
    inventory: int,
    engine_version: str,
) -> None:
    """
    Record first time inventory becomes positive.

    Rule:
        inventory > 0 AND no existing row
    """

    if inventory <= 0:
        return

    # Check if row already exists
    existing = (
        supabase
        .schema("preorder")
        .table("inventory_arrival")
        .select("product_id")
        .eq("product_id", product_id)
        .limit(1)
        .execute()
    )

    if existing.data:
        return  # idempotent: already recorded

    payload = {
        "product_id": product_id,
        "first_positive_inventory_at": datetime.now(UTC).isoformat(),
        "engine_version": engine_version,
    }

    (
        supabase
        .schema("preorder")
        .table("inventory_arrival")
        .insert(payload)
        .execute()
    )