from fastapi import APIRouter, HTTPException
from app.models.area import Area, AreaCreate
from app.services.db import get_pool, row_to_dict
from app.config import get_settings

router = APIRouter(prefix="/areas", tags=["areas"])


@router.post("", response_model=Area, status_code=201)
async def create_area(body: AreaCreate) -> Area:
    settings = get_settings()
    bbox_w = body.max_lon - body.min_lon
    bbox_h = body.max_lat - body.min_lat

    if bbox_w > settings.max_bbox_degrees or bbox_h > settings.max_bbox_degrees:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Bounding box too large ({bbox_w:.2f}°×{bbox_h:.2f}°). "
                f"Max allowed: {settings.max_bbox_degrees}°. "
                "Large areas span multiple Sentinel-2 tiles and are not supported in MVP."
            ),
        )

    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO areas (name, min_lon, min_lat, max_lon, max_lat)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        body.name, body.min_lon, body.min_lat, body.max_lon, body.max_lat,
    )
    return Area(**row_to_dict(row))


@router.get("", response_model=list[Area])
async def list_areas() -> list[Area]:
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM areas ORDER BY created_at DESC")
    return [Area(**row_to_dict(row)) for row in rows]


@router.get("/{area_id}", response_model=Area)
async def get_area(area_id: str) -> Area:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM areas WHERE id = $1", area_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Area not found")

    return Area(**row_to_dict(row))
