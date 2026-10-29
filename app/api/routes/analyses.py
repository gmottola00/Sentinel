"""
analyses.py — POST trigger full pipeline, GET list analyses.
The full pipeline is:
  1. Create analysis record (status=processing)
  2. Find Sentinel-2 products (before/after)
  3. Download bands
  4. Compute NDVI + diff
  5. Render images for Claude
  6. Claude Vision + tool use → classification
  7. Save alert
  8. Update analysis (status=done)
"""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.config import get_settings
from app.models.analysis import Analysis, AnalysisCreate
from app.services import raster
from app.services.alert_writer import save_alert
from app.services.claude_analyzer import ClaudeAnalyzer
from app.services.copernicus import CopernicusService
from app.services.db import get_pool, row_to_dict


def _analysis_dict(row) -> dict:
    """asyncpg returns JSONB columns as JSON strings — decode for the Pydantic model."""
    d = row_to_dict(row)
    if isinstance(d.get("ndvi_stats"), str):
        d["ndvi_stats"] = json.loads(d["ndvi_stats"])
    return d


router = APIRouter(prefix="/analyses", tags=["analyses"])
logger = logging.getLogger(__name__)


@router.post("", response_model=Analysis, status_code=202)
async def trigger_analysis(
    body: AnalysisCreate,
    background_tasks: BackgroundTasks,
) -> Analysis:
    """
    Trigger the full satellite change-detection pipeline asynchronously.
    Returns immediately with status=processing; poll GET /analyses/{id} for status.
    """
    pool = get_pool()

    # Verify area exists
    area = await pool.fetchrow("SELECT * FROM areas WHERE id = $1", body.area_id)
    if area is None:
        raise HTTPException(status_code=404, detail="Area not found")
    area = row_to_dict(area)

    # Create analysis record
    analysis = await pool.fetchrow(
        """
        INSERT INTO analyses (area_id, date_before, date_after, status)
        VALUES ($1, $2, $3, 'processing')
        RETURNING *
        """,
        body.area_id,
        body.date_before,
        body.date_after,
    )
    if analysis is None:
        raise HTTPException(status_code=500, detail="Failed to create analysis record")

    analysis = _analysis_dict(analysis)
    analysis_id = analysis["id"]

    # Run pipeline in background
    background_tasks.add_task(
        _run_pipeline,
        analysis_id=analysis_id,
        area=area,
        date_before=body.date_before,
        date_after=body.date_after,
    )

    return Analysis(**analysis)


@router.get("", response_model=list[Analysis])
async def list_analyses(area_id: str | None = None) -> list[Analysis]:
    pool = get_pool()
    if area_id:
        rows = await pool.fetch(
            "SELECT * FROM analyses WHERE area_id = $1 ORDER BY created_at DESC LIMIT 50",
            area_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM analyses ORDER BY created_at DESC LIMIT 50"
        )
    return [Analysis(**_analysis_dict(row)) for row in rows]


@router.get("/{analysis_id}", response_model=Analysis)
async def get_analysis(analysis_id: str) -> Analysis:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM analyses WHERE id = $1", analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return Analysis(**_analysis_dict(row))


async def _run_pipeline(
    analysis_id: str,
    area: dict,
    date_before,
    date_after,
) -> None:
    """Background task: full satellite analysis pipeline."""
    pool = get_pool()
    settings = get_settings()
    cdse = CopernicusService()
    analyzer = ClaudeAnalyzer()

    work_dir = Path(settings.temp_dir) / analysis_id
    work_dir.mkdir(parents=True, exist_ok=True)

    min_lon = area["min_lon"]
    min_lat = area["min_lat"]
    max_lon = area["max_lon"]
    max_lat = area["max_lat"]

    try:
        logger.info(f"[{analysis_id}] Starting pipeline")

        # Step 2: Find products
        logger.info(f"[{analysis_id}] Searching for BEFORE product ({date_before})")
        product_before = await cdse.find_product(
            min_lon, min_lat, max_lon, max_lat, date_before
        )

        logger.info(f"[{analysis_id}] Searching for AFTER product ({date_after})")
        product_after = await cdse.find_product(
            min_lon, min_lat, max_lon, max_lat, date_after
        )

        # Update product IDs
        await pool.execute(
            "UPDATE analyses SET product_id_before = $1, product_id_after = $2 WHERE id = $3",
            product_before["Id"],
            product_after["Id"],
            analysis_id,
        )

        # Step 3: Download bands
        logger.info(f"[{analysis_id}] Downloading BEFORE bands")
        before_dir = work_dir / "before"
        bands_before = await cdse.download_bands(
            product_before["Id"], product_before["Name"], before_dir
        )

        logger.info(f"[{analysis_id}] Downloading AFTER bands")
        after_dir = work_dir / "after"
        bands_after = await cdse.download_bands(
            product_after["Id"], product_after["Name"], after_dir
        )

        # Step 4: Compute NDVI (in thread pool — CPU-bound)
        logger.info(f"[{analysis_id}] Computing NDVI")
        bbox_args = (min_lon, min_lat, max_lon, max_lat)

        ndvi_before = await asyncio.to_thread(
            raster.compute_ndvi, bands_before["B04"], bands_before["B08"], *bbox_args
        )
        ndvi_after = await asyncio.to_thread(
            raster.compute_ndvi, bands_after["B04"], bands_after["B08"], *bbox_args
        )

        # Step 5: Compute diff stats
        stats_dict = await asyncio.to_thread(
            raster.compute_ndvi_diff, ndvi_before, ndvi_after
        )
        diff_array = stats_dict.pop("diff_array")  # don't store array in DB

        await pool.execute(
            "UPDATE analyses SET ndvi_stats = $1 WHERE id = $2",
            json.dumps(stats_dict),
            analysis_id,
        )

        # Step 6: Render images for Claude
        logger.info(f"[{analysis_id}] Rendering images")
        rgb_before_b64 = await asyncio.to_thread(
            raster.render_rgb_b64,
            bands_before["B04"],
            bands_before["B08"],
            bands_before["B02"],
            bands_before["B03"],
            *bbox_args,
        )
        rgb_after_b64 = await asyncio.to_thread(
            raster.render_rgb_b64,
            bands_after["B04"],
            bands_after["B08"],
            bands_after["B02"],
            bands_after["B03"],
            *bbox_args,
        )
        diff_image_b64 = await asyncio.to_thread(
            raster.render_diff_image_b64, diff_array
        )

        # Step 7: Claude analysis
        logger.info(f"[{analysis_id}] Sending to Claude for analysis")
        claude_result = await asyncio.to_thread(
            analyzer.analyze_change,
            rgb_before_b64,
            rgb_after_b64,
            diff_image_b64,
            stats_dict,
            area["name"],
            {
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat,
            },
        )

        classification = claude_result["classification"]
        raw = claude_result["raw_claude_response"]

        # Step 8: Save alert
        await save_alert(
            analysis_id=analysis_id,
            area_id=area["id"],
            classification=classification,
            raw_claude_response=raw,
        )

        # Step 9: Mark done
        await pool.execute(
            "UPDATE analyses SET status = 'done' WHERE id = $1", analysis_id
        )
        logger.info(f"[{analysis_id}] Pipeline complete")

    except Exception as exc:
        logger.exception(f"[{analysis_id}] Pipeline failed")
        await pool.execute(
            "UPDATE analyses SET status = 'error', error_message = $1 WHERE id = $2",
            str(exc)[:500],
            analysis_id,
        )
    finally:
        await cdse.aclose()
