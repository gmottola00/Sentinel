"""
Standalone test script for Copernicus download + NDVI rendering.
Run with: uv run python scripts/test_download.py

Tests:
  1. OAuth2 token fetch
  2. Product search for a known Amazonia tile
  3. Band download
  4. NDVI computation
  5. PNG rendering (saved to /tmp/sentinel-watch/test_output/)
"""
import asyncio
import logging
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

# Test area: south Amazonia (small, ~0.5°×0.5°)
MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = -55.5, -5.0, -55.0, -4.5
DATE_BEFORE = date(2022, 7, 1)
DATE_AFTER  = date(2023, 7, 1)
OUT_DIR = Path("/tmp/sentinel-watch/test_output")


async def main():
    from app.services.copernicus import CopernicusService
    from app.services import raster
    import base64

    cdse = CopernicusService()

    try:
        # 1. Find products
        logger.info("Searching for BEFORE product...")
        p_before = await cdse.find_product(MIN_LON, MIN_LAT, MAX_LON, MAX_LAT, DATE_BEFORE)
        logger.info(f"BEFORE: {p_before['Name']}")

        logger.info("Searching for AFTER product...")
        p_after = await cdse.find_product(MIN_LON, MIN_LAT, MAX_LON, MAX_LAT, DATE_AFTER)
        logger.info(f"AFTER:  {p_after['Name']}")

        # 2. Download bands
        before_dir = OUT_DIR / "before"
        after_dir  = OUT_DIR / "after"

        logger.info("Downloading BEFORE bands (this may take several minutes)...")
        bands_before = await cdse.download_bands(p_before["Id"], p_before["Name"], before_dir)
        logger.info(f"Downloaded: {list(bands_before.keys())}")

        logger.info("Downloading AFTER bands...")
        bands_after = await cdse.download_bands(p_after["Id"], p_after["Name"], after_dir)
        logger.info(f"Downloaded: {list(bands_after.keys())}")

        # 3. Compute NDVI
        logger.info("Computing NDVI...")
        bbox = (MIN_LON, MIN_LAT, MAX_LON, MAX_LAT)
        ndvi_before = raster.compute_ndvi(bands_before["B04"], bands_before["B08"], *bbox)
        ndvi_after  = raster.compute_ndvi(bands_after["B04"],  bands_after["B08"],  *bbox)

        stats = raster.compute_ndvi_diff(ndvi_before, ndvi_after)
        diff_arr = stats.pop("diff_array")
        logger.info(f"NDVI stats: {stats}")

        # 4. Render images
        logger.info("Rendering RGB and diff images...")
        rgb_before_b64 = raster.render_rgb_b64(
            bands_before["B04"], bands_before["B08"],
            bands_before["B02"], bands_before["B03"], *bbox
        )
        rgb_after_b64 = raster.render_rgb_b64(
            bands_after["B04"], bands_after["B08"],
            bands_after["B02"], bands_after["B03"], *bbox
        )
        diff_b64 = raster.render_diff_image_b64(diff_arr)

        # Save PNGs
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "rgb_before.png").write_bytes(base64.b64decode(rgb_before_b64))
        (OUT_DIR / "rgb_after.png").write_bytes(base64.b64decode(rgb_after_b64))
        (OUT_DIR / "diff.png").write_bytes(base64.b64decode(diff_b64))
        logger.info(f"Saved PNGs to {OUT_DIR}")

        print("\n=== TEST PASSED ===")
        print(f"NDVI stats: {stats}")
        print(f"PNGs: {OUT_DIR}/rgb_before.png, rgb_after.png, diff.png")

    except Exception as e:
        logger.exception(f"Test FAILED: {e}")
        sys.exit(1)
    finally:
        await cdse.aclose()


if __name__ == "__main__":
    asyncio.run(main())
