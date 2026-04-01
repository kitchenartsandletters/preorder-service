# classification/types.py

from dataclasses import dataclass
from datetime import date
from typing import List, Optional

@dataclass
class ClassificationInput:
    product_id: int
    tags: List[str]
    in_preorder_collection: bool
    date_tags: List[date]
    pub_date: Optional[date]
    override_date: Optional[date]
    inventory: int
    has_inventory_arrival: bool = False   # New field to indicate if there's an inventory arrival event for this product


@dataclass
class ClassificationResult:
    status: str                       # "active_preorder" | "historical_preorder" | "anomaly_*"
    anomaly_type: Optional[str]
    effective_pub_date: Optional[date]


# classification/engine.py

from .types import ClassificationInput, ClassificationResult

def classify_preorder_product(inp: ClassificationInput) -> ClassificationResult:
    """
    Contract-only skeleton.
    Implementation must follow Preorder Classification Specification.md.
    """
    # TODO: IMPLEMENT — this thread only defines structure
    return ClassificationResult(
        status="anomaly_missing_tag",   # placeholder
        anomaly_type="not_implemented",
        effective_pub_date=None
    )