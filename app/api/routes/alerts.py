import json

from fastapi import APIRouter, HTTPException, Query
from app.models.alert import Alert
from app.services.db import get_pool, row_to_dict

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[Alert])
async def list_alerts(
    area_id: str | None = Query(None, description="Filter by area"),
    limit: int = Query(50, ge=1, le=200),
) -> list[Alert]:
    pool = get_pool()
    if area_id:
        rows = await pool.fetch(
            "SELECT * FROM alerts WHERE area_id = $1 ORDER BY created_at DESC LIMIT $2",
            area_id, limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT $1", limit
        )
    return [Alert(**_alert_dict(row)) for row in rows]


@router.get("/{alert_id}", response_model=Alert)
async def get_alert(alert_id: str) -> Alert:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM alerts WHERE id = $1", alert_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    return Alert(**_alert_dict(row))


def _alert_dict(row) -> dict:
    """asyncpg returns JSONB columns as JSON strings — decode for the Pydantic model."""
    d = row_to_dict(row)
    if isinstance(d.get("raw_claude_response"), str):
        d["raw_claude_response"] = json.loads(d["raw_claude_response"])
    return d
