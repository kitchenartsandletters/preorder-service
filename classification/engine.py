"""
classification/engine.py

Preorder Classification Engine (Scaffolding Only)

This file defines the public classifier entry point:
    classify_preorder_product(product: ProductMetadata) -> ClassificationResult

IMPORTANT:
- This file intentionally DOES NOT implement classification logic.
- All TODO blocks reference the canonical rules in the
  "Preorder Classification Specification.md" contract.
- The implementation will be added in a later step once pytest scaffolding is prepared.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

# Local imports – these must exist in the repo
from .types import ClassificationInput, ClassificationResult
from .utils import resolve_effective_pub_date

# --------------------------------------------------------------------------------------
# Helper predicates / detectors (Phase 6 refactor)
# NOTE: These functions MUST remain side-effect free.
# --------------------------------------------------------------------------------------

def _has_preorder_tag(product: ClassificationInput) -> bool:
    return "preorder" in product.tags


def _is_in_preorder_collection(product: ClassificationInput) -> bool:
    return bool(product.in_preorder_collection)


def _is_structurally_preorder(product: ClassificationInput) -> bool:
    # Structural preorder identity gate: tag OR collection membership
    return _has_preorder_tag(product) or _is_in_preorder_collection(product)


def _is_held(product: ClassificationInput, effective_pub_date: date | None) -> bool:
    """
    A held title stays an active preorder past its pub date because somebody
    decided so: a signing (`event`) or stock genuinely in transit
    (`awaiting_stock`). The hold lives in preorder.preorder_hold and carries an
    end date; `has_active_hold` is True only while it is live, evaluated in ET
    by services/hold_service.py.

    This REPLACES the old inferred "delayed import" rule, which treated
    (past pub date AND no inventory arrival AND inventory <= 0) as a hold and
    kept such titles active indefinitely. Owner decision 2026-10-15: that
    inference is wrong. A past-dated title with no stock and no hold is a
    historical preorder with unresolved commitments, and if it is still in the
    Preorder collection it needs cleaning up (anomaly_stale_collection).

    Deliberately NOT conditioned on inventory: an `event` hold usually has stock
    on hand — the book released and the signing is still to come — and the
    incentive should keep running. Collection membership IS required: the
    collection drives the rewards system, so a title removed from it is no
    longer being held in any meaningful sense.
    """
    return (
        product.has_active_hold
        and _is_structurally_preorder(product)
        and _is_in_preorder_collection(product)
        and effective_pub_date is not None
        and effective_pub_date < date.today()
    )


def _detect_anomaly(
    product: ClassificationInput,
    effective_pub_date: date | None,
) -> ClassificationResult | None:
    """
    Detect anomaly classifications in strict priority order.
    Returns a ClassificationResult for the first matching anomaly, else None.

    IMPORTANT: Ordering is part of the canonical contract — do not reorder.
    """

    # Phase 2.0: anomaly_stale_collection
    # A product whose pub date has passed but is still in the Preorder collection.
    # The preorder tag is intentionally retained on historical titles as a marker,
    # so tag presence alone is not the signal — collection membership is.
    #
    # It no longer requires has_inventory_arrival. That condition existed to protect
    # inferred "delayed imports" (past pub date, no stock) from being flagged for
    # cleanup. Holds are explicit now, so the exemption is `has_active_hold`: a held
    # title is in the collection on purpose. Anything else past its pub date and still
    # in the collection needs removing, whether or not stock ever arrived.
    if (
        _is_in_preorder_collection(product)
        and _has_preorder_tag(product)
        and not product.has_active_hold
        and effective_pub_date is not None
        and effective_pub_date < date.today()
        and (not product.date_tags or product.pub_date == max(product.date_tags))
    ):
        return ClassificationResult(
            status="anomaly_stale_collection",
            anomaly_type="anomaly_stale_collection",
            effective_pub_date=effective_pub_date,
        )

    # Phase 2.1: anomaly_missing_tag
    if _is_in_preorder_collection(product) and not _has_preorder_tag(product):
        return ClassificationResult(
            status="anomaly_missing_tag",
            anomaly_type="anomaly_missing_tag",
            effective_pub_date=effective_pub_date,
        )

    # New rule (inventory arrival exemption):
    if (
        _has_preorder_tag(product)
        and not _is_in_preorder_collection(product)
        and effective_pub_date is not None
        and effective_pub_date >= date.today()
        and not product.has_inventory_arrival   # ← new field on ClassificationInput
    ):
        return ClassificationResult(
            status="anomaly_missing_collection",
            anomaly_type="anomaly_missing_collection",
            effective_pub_date=effective_pub_date,
        )

    # Phase 2.3 (anomaly_override_conflict) was REMOVED in rev2 Move 1e.
    # The override metafield is retired: its values were folded into pub_date on
    # 2026-10-27, so the three conflict cases (override earlier than pub_date,
    # earlier than the latest date tag, or past while pub_date is future) can no
    # longer arise. pub_date is the single source of truth (decision D1).

    # Phase 2.4: anomaly_pubdate_conflict
    # If pub_date disagrees with latest date_tag
    if product.pub_date is not None and product.date_tags:
        latest_tag = max(product.date_tags)

        if product.pub_date != latest_tag:
            return ClassificationResult(
                status="anomaly_pubdate_conflict",
                anomaly_type="anomaly_pubdate_conflict",
                effective_pub_date=effective_pub_date,
            )

    # Phase 2.5: anomaly_multi_date_conflict
    if (
        product.pub_date is None
        and len(product.date_tags) >= 2
    ):
        return ClassificationResult(
            status="anomaly_multi_date_conflict",
            anomaly_type="anomaly_multi_date_conflict",
            effective_pub_date=effective_pub_date,
        )

    return None


def _is_early_stock_arrival(product: ClassificationInput, effective_pub_date: date | None) -> bool:
    # Standard path: tag + collection + future date + inventory
    standard = (
        _is_structurally_preorder(product)
        and effective_pub_date is not None
        and effective_pub_date >= date.today()
        and product.inventory > 0
    )

    # PDP cleanup path: tag present, collection removed after inventory arrived.
    # inventory_arrival record is the authoritative signal that removal was intentional.
    pdp_cleanup = (
        _has_preorder_tag(product)
        and not _is_in_preorder_collection(product)
        and effective_pub_date is not None
        and effective_pub_date >= date.today()
        and product.has_inventory_arrival
        and product.inventory >= 0 
    )

    return standard or pdp_cleanup


def _is_active_preorder(product: ClassificationInput, effective_pub_date: date | None) -> bool:
    # Standard path: structurally preorder, future-dated, no stock yet.
    standard = (
        _is_structurally_preorder(product)
        and effective_pub_date is not None
        and effective_pub_date >= date.today()
        and product.inventory <= 0
    )

    # Held path: the pub date has passed but an explicit, unexpired hold keeps the
    # title an active preorder (and in the Preorder collection, which is what drives
    # the rewards system). See _is_held.
    held = _is_held(product, effective_pub_date)

    return standard or held


def _is_historical_preorder(product: ClassificationInput, effective_pub_date: date | None) -> bool:
    return (
        _has_preorder_tag(product)
        and not _is_in_preorder_collection(product)
        and (effective_pub_date is None or effective_pub_date <= date.today())
    )


def classify_preorder_product(product: ClassificationInput) -> ClassificationResult:
    """
    Classify a single Shopify product into:

        - active_preorder
        - historical_preorder
        - anomaly_*

    using the canonical rules defined in:
        Preorder Classification Specification.md

    PARAMETERS
    ----------
    product : ClassificationInput
        Dataclass containing:
            product_id: int
            tags: List[str]
            in_preorder_collection: bool
            date_tags: List[date]
            pub_date: Optional[date]
            inventory: int

    RETURNS
    -------
    ClassificationResult
        Contains:
            status: str
            anomaly_type: Optional[str]
            effective_pub_date: Optional[date]

    NOTES
    -----
    This function is intentionally NOT implemented.
    All logic must be added in the Implementation Phase
    after the pytest suite is scaffolded.

    TODO IMPLEMENTATION CHECKLIST
    ------------------------------

    1. EFFECTIVE PUB DATE RESOLUTION
       - Determine `effective_pub_date` using strict priority:
            1) pub_date
            2) latest date_tag
            3) None
       - Validate date ordering
       - Detect pubdate conflicts (anomaly_pubdate_conflict)

    2. ANOMALY DETECTION (IF ANY ANOMALY FOUND → RETURN IMMEDIATELY)
       Implement all categories from the specification:

        • anomaly_missing_tag
        • anomaly_missing_collection
        • anomaly_pubdate_conflict
        • anomaly_multi_date_conflict
        • anomaly_inventory_contradiction
        • anomaly_stale_collection  — pub date passed, still in Preorder collection

       Rules must directly reflect Section 4 of the spec.

    3. ACTIVE PREORDER
       A product is active if:
          any future-dated signal is true AND no anomalies

       Future-dated signals include:
          - in_preorder_collection
          - preorder tag
          - any future date_tag
          - pub_date in the future

    4. HISTORICAL PREORDER
       Valid only when:
          - preorder tag present
          - all dates in the past
          - NOT in preorder collection
          - inventory normal
          - no anomalies

    5. DEFAULT FALLBACK
       In theory unreachable if all rules are implemented.
       Must return a deterministic result if logic fails silently.

    ----------------------------------------------------------------
    END OF TODO BLOCK — Do not modify until pytest suite is ready.
    """

    # Phase 1: Effective publication date resolution (read-only wiring)
    effective_pub_date = resolve_effective_pub_date(
        date_tags=product.date_tags,
        pub_date=product.pub_date,
    )

    # ----------------------------------------------------------------------------------
    # CLASSIFICATION ORDER (DO NOT REORDER):
    # 1. anomaly checks (all anomaly_* types)
    # 2. early_stock_arrival
    # 3. active_preorder
    # 4. historical_preorder
    # 5. not_a_preorder_product
    # ----------------------------------------------------------------------------------

    anomaly = _detect_anomaly(product, effective_pub_date)
    if anomaly is not None:
        return anomaly

    # Phase 3: early_stock_arrival
    if _is_early_stock_arrival(product, effective_pub_date):
        return ClassificationResult(
            status="early_stock_arrival",
            anomaly_type=None,
            effective_pub_date=effective_pub_date,
        )

    # Phase 4: active_preorder
    if _is_active_preorder(product, effective_pub_date):
        return ClassificationResult(
            status="active_preorder",
            anomaly_type=None,
            effective_pub_date=effective_pub_date,
        )

    # Phase 5: historical_preorder
    if _is_historical_preorder(product, effective_pub_date):
        return ClassificationResult(
            status="historical_preorder",
            anomaly_type=None,
            effective_pub_date=effective_pub_date,
        )

    # Phase 6: not_a_preorder_product (deterministic fallback)
    return ClassificationResult(
        status="not_a_preorder_product",
        anomaly_type=None,
        effective_pub_date=effective_pub_date,
    )
