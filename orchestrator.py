"""
If you later decide you do want to record “pub date removed” as an event, the correct way would be:
	•	change the schema to allow NULL new_effective_pub_date, or
	•	record removals in a different column / event table.
"""

import logging
from typing import List, Optional

from classification.engine import classify_preorder_product
from services import hold_service
from classification.engine import ClassificationInput, ClassificationResult
from domain_models import ProductMetadata
from persistence import persist_classification, persist_inventory_arrival
from datetime import date
from datetime import datetime, UTC

logger = logging.getLogger(__name__)


def _consume_write_intent(supabase, product_id: int, new_pub_date) -> Optional[dict]:
    """
    Claim an open write intent matching this product and date, if one exists.

    The sanctioned pub-date editor (rev2 Move 1c) records an intent immediately
    before calling metafieldsSet. Shopify then sends products/update for that
    same edit, and without the intent this orchestrator would label it
    shopify_direct_edit — i.e. report the dashboard's own write as an
    out-of-band change. Claiming the intent labels it ui_change and carries the
    operator's identity and reason into the history row.

    Returns the claimed intent, or None when the change did not come from the
    dashboard (a genuine direct edit in Shopify admin).
    """
    if new_pub_date is None:
        return None
    try:
        open_intents = (
            supabase.schema("preorder")
            .table("pubdate_write_intent")
            .select("id, changed_by, reason, created_at")
            .eq("product_id", product_id)
            .eq("new_date", new_pub_date.isoformat())
            .is_("consumed_at", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = open_intents.data or []
        if not rows:
            return None
        intent = rows[0]
        supabase.schema("preorder").table("pubdate_write_intent").update(
            {"consumed_at": datetime.now(UTC).isoformat()}
        ).eq("id", intent["id"]).execute()
        return intent
    except Exception as exc:
        # A missing intent must never block classification: worst case the
        # change is labelled shopify_direct_edit, which is merely less precise.
        logger.warning(f"[orchestrator] write-intent lookup failed for {product_id}: {exc}")
        return None


def classify_and_persist_product(
    supabase,
    product_metadata: ProductMetadata,
    engine_version: str = "v1",
    change_source: Optional[str] = None,
    change_metadata: Optional[dict] = None,
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

    # The override metafield and preorder.product_overrides are retired
    # (rev2 Move 1e). Values were folded into pub_date on 2026-10-27, so
    # pub_date is the single source of truth. override_service and the table
    # itself are removed in 1g.

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

    # Explicit, unexpired hold (preorder.preorder_hold). Replaces the engine's old
    # inferred "delayed import" rule; see services/hold_service.py.
    has_active_hold = hold_service.has_active_hold(supabase, product_metadata.product_id)

    engine_input = ClassificationInput(
        product_id=product_metadata.product_id,
        tags=product_metadata.tags,
        in_preorder_collection=product_metadata.in_preorder_collection,
        date_tags=product_metadata.parsed_date_tags(),
        pub_date=_parse_date(product_metadata.pub_date_raw),
        inventory=product_metadata.inventory,
        has_inventory_arrival=has_inventory_arrival,
        has_active_hold=has_active_hold,
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
            # Who changed it?
            #   - an explicit change_source from a caller (the pub-date editor
            #     passes "ui_change") always wins;
            #   - otherwise an open write intent means the dashboard made this
            #     edit and Shopify is echoing it back;
            #   - otherwise somebody edited the product in Shopify admin, which
            #     is recorded, not rejected (rev2 D9).
            # "shopify_pub_date" is retired here: it described the mechanism
            # rather than the actor, and said nothing about who made the change.
            row_metadata = dict(change_metadata or {})
            if change_source:
                resolved_source = change_source
            else:
                intent = _consume_write_intent(
                    supabase, product_metadata.product_id, normalized_resolved_pub_date
                )
                if intent:
                    resolved_source = "ui_change"
                    row_metadata.setdefault("changed_by", intent.get("changed_by"))
                    row_metadata.setdefault("reason", intent.get("reason"))
                    row_metadata.setdefault("intent_id", intent.get("id"))
                else:
                    resolved_source = "shopify_direct_edit"

            history_row = {
                "product_id": product_metadata.product_id,
                "old_effective_pub_date": stored_pub_date.isoformat() if stored_pub_date else None,
                "new_effective_pub_date": normalized_resolved_pub_date.isoformat(),
                "change_source": resolved_source,
                "engine_version": engine_version,
                "changed_at": datetime.now(UTC).isoformat(),
            }
            if row_metadata:
                history_row["metadata"] = row_metadata

            (
                supabase
                .schema("preorder")
                .table("pubdate_history")
                .insert(history_row)
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