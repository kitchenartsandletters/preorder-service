from typing import List

from classification.engine import classify_preorder_product
from classification.engine import ClassificationInput, ClassificationResult
from domain_models import ProductMetadata
from persistence import persist_classification
from datetime import date
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
    )

    result = classify_preorder_product(engine_input)

    persist_classification(
        supabase=supabase,
        product_id=product_metadata.product_id,
        classification=result,
        metadata_snapshot=engine_input.__dict__,
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