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

    # Placeholder output to keep the module importable and runnable.
    # This will be replaced after test-driven implementation begins.
    return ClassificationResult(
        status="anomaly_missing_tag",
        anomaly_type="not_implemented",
        effective_pub_date=None,
    )