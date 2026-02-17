from pydantic import BaseModel
from typing import Optional


class ReclassifyResponse(BaseModel):
    product_id: int
    status: str
    anomaly_type: Optional[str]
    effective_pub_date: Optional[str]
    engine_version: str

class BatchReclassifyResponse(BaseModel):
    total_requested: int
    total_processed: int
    results: list[ReclassifyResponse]