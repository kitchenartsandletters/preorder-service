# override_service.py

from typing import Optional, Dict, Any
from datetime import datetime, timezone

from domain_models import ProductMetadata


def fetch_override_date(supabase, product_id: int) -> Optional[str]:
    """
    Fetch override_date_raw from preorder.product_overrides.

    Returns None if no row exists.
    """

    response = (
        supabase.schema("preorder")
        .table("product_overrides")
        .select("override_date_raw")
        .eq("product_id", product_id)
        .maybe_single()
        .execute()
    )

    if not response or not getattr(response, "data", None):
        return None

    return response.data.get("override_date_raw")


def update_override_date_and_reclassify(
    supabase,
    product_metadata: ProductMetadata,
    new_override_date_raw: Optional[str],
    engine_version: str,
    actor: Optional[str] = None,
) -> Dict[str, Any]:

    # 1️⃣ Upsert override row
    supabase.schema("preorder").table("product_overrides").upsert(
        {
            "product_id": product_metadata.product_id,
            "override_date_raw": new_override_date_raw,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": actor,
        },
        on_conflict="product_id",
    ).execute()

    # 2️⃣ Update local domain object
    product_metadata.override_date_raw = new_override_date_raw

    # 3️⃣ Re-run classification
    # Local import avoids circular import
    from reclassification import reclassify_single_product

    return reclassify_single_product(
        supabase=supabase,
        product_metadata=product_metadata,
        engine_version=engine_version,
    )