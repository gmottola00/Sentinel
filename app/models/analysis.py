from datetime import date, datetime
from typing import Any, Optional
from pydantic import BaseModel


class AnalysisCreate(BaseModel):
    area_id: str
    date_before: date
    date_after: date


class Analysis(BaseModel):
    id: str
    area_id: str
    date_before: date
    date_after: date
    product_id_before: Optional[str] = None
    product_id_after: Optional[str] = None
    ndvi_stats: Optional[dict[str, Any]] = None
    status: str  # pending | processing | done | error
    error_message: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
