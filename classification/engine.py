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


def _detect_anomaly(
    product: ClassificationInput,
    effective_pub_date: date | None,
) -> ClassificationResult | None:
    """
    Detect anomaly classifications in strict priority order.
    Returns a ClassificationResult for the first matching anomaly, else None.

    IMPORTANT: Ordering is part of the canonical contract — do not reorder.
    """

    # Phase 2.1: anomaly_missing_tag
    if _is_in_preorder_collection(product) and not _has_preorder_tag(product):
        return ClassificationResult(
            status="anomaly_missing_tag",
            anomaly_type="anomaly_missing_tag",
            effective_pub_date=effective_pub_date,
        )

    # Phase 2.2: anomaly_missing_collection
    if (
        _has_preorder_tag(product)
        and not _is_in_preorder_collection(product)
        and effective_pub_date is not None
        and effective_pub_date > date.today()
    ):
        return ClassificationResult(
            status="anomaly_missing_collection",
            anomaly_type="anomaly_missing_collection",
            effective_pub_date=effective_pub_date,
        )

    # Phase 2.3: anomaly_override_conflict
    if product.override_date is not None:
        # Case 1: override earlier than pub_date
        if product.pub_date is not None and product.override_date < product.pub_date:
            return ClassificationResult(
                status="anomaly_override_conflict",
                anomaly_type="anomaly_override_conflict",
                effective_pub_date=effective_pub_date,
            )

        # Case 2: override earlier than latest date_tag
        if product.date_tags and product.override_date < max(product.date_tags):
            return ClassificationResult(
                status="anomaly_override_conflict",
                anomaly_type="anomaly_override_conflict",
                effective_pub_date=effective_pub_date,
            )

        # Case 3: override in the past while pub_date is in the future
        if (
            product.pub_date is not None
            and product.override_date < date.today()
            and product.pub_date > date.today()
        ):
            return ClassificationResult(
                status="anomaly_override_conflict",
                anomaly_type="anomaly_override_conflict",
                effective_pub_date=effective_pub_date,
            )

    # Phase 2.4: anomaly_pubdate_conflict
    # If pub_date disagrees with latest date_tag (regardless of override existence,
    # since override conflicts are handled in Phase 2.3)
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
        product.override_date is None
        and product.pub_date is None
        and len(product.date_tags) >= 2
    ):
        return ClassificationResult(
            status="anomaly_multi_date_conflict",
            anomaly_type="anomaly_multi_date_conflict",
            effective_pub_date=effective_pub_date,
        )

    return None


def _is_early_stock_arrival(product: ClassificationInput, effective_pub_date: date | None) -> bool:
    return (
        _is_structurally_preorder(product)
        and effective_pub_date is not None
        and effective_pub_date > date.today()
        and product.inventory > 0
    )


def _is_active_preorder(product: ClassificationInput, effective_pub_date: date | None) -> bool:
    return (
        _is_structurally_preorder(product)
        and effective_pub_date is not None
        and effective_pub_date > date.today()
        and product.inventory <= 0
    )


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
            override_date: Optional[date]
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
            1) override_date
            2) pub_date
            3) earliest date_tag
            4) None
       - Validate date ordering
       - Detect pubdate conflicts (anomaly_pubdate_conflict)

    2. ANOMALY DETECTION (IF ANY ANOMALY FOUND → RETURN IMMEDIATELY)
       Implement all categories from the specification:

        • anomaly_missing_tag
        • anomaly_missing_collection
        • anomaly_pubdate_conflict
        • anomaly_override_conflict
        • anomaly_multi_date_conflict
        • anomaly_inventory_contradiction

       Rules must directly reflect Section 4 of the spec.

    3. ACTIVE PREORDER
       A product is active if:
          any future-dated signal is true AND no anomalies

       Future-dated signals include:
          - in_preorder_collection
          - preorder tag
          - any future date_tag
          - pub_date or override_date in the future

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
        override_date=product.override_date,
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