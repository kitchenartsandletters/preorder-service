"""
If you later decide you do want to record “pub date removed” as an event, the correct way would be:
	•	change the schema to allow NULL new_effective_pub_date, or
	•	record removals in a different column / event table.
"""

from typing import List

from classification.engine import classify_preorder_product
from classification.engine import ClassificationInput, ClassificationResult
from domain_models import ProductMetadata
from persistence import persist_classification, persist_inventory_arrival
from datetime import date
from datetime import datetime, UTC
from override_service import fetch_override_date

def classify_and_persist_product(
    supabase,
    product_metadata: ProductMetadata,
    engine_version: str = "v1",
) -> ClassificationResult:
    """
    Deterministic orchestration layer.

    Responsibilities:
    - Convert domain model → ClassificationInput
    - Call classification engine
    - Persist result
    - Return ClassificationResult

    No Shopify logic.
    No side effects beyond persistence.
    """

    # Fetch authoritative override from DB (if any)
    override_from_db = fetch_override_date(
        supabase=supabase,
        product_id=product_metadata.product_id,
    )

    if override_from_db is not None:
        product_metadata.override_date_raw = override_from_db

    # Parse raw ISO date strings into date objects for engine
    def _parse_date(raw):
        if raw is None:
            return None
        if isinstance(raw, date):
            return raw
        if isinstance(raw, str):
            return date.fromisoformat(raw)
        return None

    arrival_response = (
    supabase
    .schema("preorder")
    .table("inventory_arrival")
    .select("product_id")
    .eq("product_id", product_metadata.product_id)
    .limit(1)
    .execute()
)
    has_inventory_arrival = bool(arrival_response.data)

    engine_input = ClassificationInput(
        product_id=product_metadata.product_id,
        tags=product_metadata.tags,
        in_preorder_collection=product_metadata.in_preorder_collection,
        date_tags=product_metadata.parsed_date_tags(),
        pub_date=_parse_date(product_metadata.pub_date_raw),
        override_date=_parse_date(
            override_from_db
            if override_from_db is not None
            else product_metadata.override_date_raw
        ),
        inventory=product_metadata.inventory,
        has_inventory_arrival=has_inventory_arrival,
    )

    result = classify_preorder_product(engine_input)

    # --- Phase 10: Pub Date History Tracking ---

    resolved_pub_date = result.effective_pub_date

    # Fetch existing product_status (if any)
    existing_response = (
        supabase
        .schema("preorder")
        .table("product_status")
        .select("effective_pub_date")
        .eq("product_id", product_metadata.product_id)
        .execute()
    )

    existing_rows = existing_response.data if hasattr(existing_response, "data") else None

    # Normalize values to `date` for comparison
    def _normalize_date(value):
        if value is None:
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return date.fromisoformat(value)
        return None

    normalized_resolved_pub_date = _normalize_date(resolved_pub_date)

    if not existing_rows:
        # Baseline initialization: only write history if we actually have a pub date.
        if normalized_resolved_pub_date is not None:
            (
                supabase
                .schema("preorder")
                .table("pubdate_history")
                .insert({
                    "product_id": product_metadata.product_id,
                    "old_effective_pub_date": None,
                    "new_effective_pub_date": normalized_resolved_pub_date.isoformat(),
                    "change_source": "initial_baseline",
                    "engine_version": engine_version,
                    "changed_at": datetime.now(UTC).isoformat(),
                })
                .execute()
            )
    else:
        stored_pub_date_raw = existing_rows[0].get("effective_pub_date")
        stored_pub_date = _normalize_date(stored_pub_date_raw)

        # If we are "losing" a pub date (new resolves to None), do NOT write a history row.
        # `pubdate_history.new_effective_pub_date` is NOT NULL.
        if stored_pub_date != normalized_resolved_pub_date and normalized_resolved_pub_date is not None:
            # Determine change source
            if engine_input.override_date is not None:
                change_source = "override_date"
            elif engine_input.pub_date is not None:
                change_source = "shopify_pub_date"
            else:
                change_source = "legacy_tag_fallback"

            (
                supabase
                .schema("preorder")
                .table("pubdate_history")
                .insert({
                    "product_id": product_metadata.product_id,
                    "old_effective_pub_date": stored_pub_date.isoformat() if stored_pub_date else None,
                    "new_effective_pub_date": normalized_resolved_pub_date.isoformat(),
                    "change_source": change_source,
                    "engine_version": engine_version,
                    "changed_at": datetime.now(UTC).isoformat(),
                })
                .execute()
            )

    # --- Phase 11: Inventory Arrival Tracking ---
    persist_inventory_arrival(
        supabase=supabase,
        product_id=product_metadata.product_id,
        inventory=product_metadata.inventory,
        engine_version=engine_version,
    )
    
    def _serialize_dates(obj):
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, dict):
            return {k: _serialize_dates(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_serialize_dates(v) for v in obj]
        return obj

    metadata_snapshot = {
        "product_id": product_metadata.product_id,
        "title": product_metadata.title,
        "vendor": product_metadata.vendor,
        "isbn": product_metadata.isbn,
        "tags": product_metadata.tags,
        "pub_date": product_metadata.pub_date_raw,
        "inventory": product_metadata.inventory,
        "override_date": product_metadata.override_date_raw,
        "in_preorder_collection": product_metadata.in_preorder_collection,
    }

    serialized_snapshot = _serialize_dates(metadata_snapshot)

    persist_classification(
        supabase=supabase,
        product_id=product_metadata.product_id,
        classification=result,
        metadata_snapshot=serialized_snapshot,
        engine_version=engine_version,
    )

    return result


def batch_reclassify(
    supabase,
    products: List[ProductMetadata],
    engine_version: str = "v1",
) -> List[ClassificationResult]:
    """
    Reclassify many products.

    Rules:
    - Always continue on failure
    - Never crash entire batch
    - Deterministic per product
    """

    results: List[ClassificationResult] = []

    for product in products:
        try:
            result = classify_and_persist_product(
                supabase=supabase,
                product_metadata=product,
                engine_version=engine_version,
            )
            results.append(result)
        except Exception:
            # Continue processing remaining products
            continue

    return results

# --- Webhook entry point ---

async def reclassify_single_product(
    supabase,
    product_metadata: ProductMetadata,
    engine_version: str = "v1",
) -> ClassificationResult:
    """
    Async-safe wrapper used by webhook layer.

    This keeps webhook routes simple while preserving
    deterministic classification behavior.
    """

    # Currently classification itself is synchronous.
    # This wrapper exists for architectural symmetry and
    # future async expansion (e.g., external lookups).

    return classify_and_persist_product(
        supabase=supabase,
        product_metadata=product_metadata,
        engine_version=engine_version,
    )