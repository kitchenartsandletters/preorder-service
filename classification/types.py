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
