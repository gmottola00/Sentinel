from datetime import date, datetime
from typing import Any

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
    product_id_before: str | None = None
    product_id_after: str | None = None
    ndvi_stats: dict[str, Any] | None = None
    status: str  # pending | processing | done | error
    error_message: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
