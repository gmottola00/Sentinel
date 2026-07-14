from datetime import datetime
from pydantic import BaseModel, Field


class AreaCreate(BaseModel):
    name: str
    min_lon: float = Field(..., ge=-180, le=180, description="West boundary (WGS84)")
    min_lat: float = Field(..., ge=-90, le=90, description="South boundary (WGS84)")
    max_lon: float = Field(..., ge=-180, le=180, description="East boundary (WGS84)")
    max_lat: float = Field(..., ge=-90, le=90, description="North boundary (WGS84)")


class Area(AreaCreate):
    id: str
    created_at: datetime

    model_config = {"from_attributes": True}
