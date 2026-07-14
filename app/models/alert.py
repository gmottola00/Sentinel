from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel


class Alert(BaseModel):
    id: str
    analysis_id: str
    area_id: str
    severity: str          # low | medium | high | critical
    change_type: str       # deforestation | flood | fire | urban_growth | agricultural | other | no_change
    confidence: float      # 0.0 – 1.0
    description: str
    raw_claude_response: Optional[dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}
