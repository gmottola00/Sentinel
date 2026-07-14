"""Write Claude's classification result as an alert to Postgres."""
import json
import logging
from typing import Any

from app.services.db import get_pool

logger = logging.getLogger(__name__)


async def save_alert(
    analysis_id: str,
    area_id: str,
    classification: dict[str, Any],
    raw_claude_response: list[dict],
) -> dict:
    """
    Insert an alert row into Postgres and return the created record.
    """
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO alerts (analysis_id, area_id, severity, change_type, confidence, description, raw_claude_response)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """,
        analysis_id,
        area_id,
        classification["severity"],
        classification["change_type"],
        float(classification["confidence"]),
        classification["description"],
        json.dumps(raw_claude_response),
    )

    if row is None:
        raise RuntimeError("Failed to insert alert into Postgres")

    alert = dict(row)
    logger.info(f"Alert saved: id={alert['id']} type={alert['change_type']} severity={alert['severity']}")
    return alert
