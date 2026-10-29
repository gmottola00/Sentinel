"""
Raster processing: NDVI computation, difference maps, PNG rendering.
All CPU-bound functions are intended to be called via asyncio.to_thread().
"""

import base64
import io
import logging
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 1024  # pixels — Claude Vision limit


def _crop_to_bbox(
    band_path: Path,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
) -> np.ndarray:
    """Open a JP2 band, crop to bbox (WGS84 → dataset CRS), return float32 array."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    with rasterio.open(band_path) as src:
        # Transform bbox from WGS84 to the dataset's CRS (usually UTM)
        bounds = transform_bounds(
            "EPSG:4326", src.crs, min_lon, min_lat, max_lon, max_lat
        )
        window = from_bounds(*bounds, transform=src.transform)
        data = src.read(1, window=window).astype(np.float32)

    # Replace nodata (0 in JP2 Sentinel-2 files) with NaN
    data[data == 0] = np.nan
    return data


def compute_ndvi(
    b04_path: Path,
    b08_path: Path,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
) -> np.ndarray:
    """Compute NDVI array from Red (B04) and NIR (B08) bands."""
    red = _crop_to_bbox(b04_path, min_lon, min_lat, max_lon, max_lat)
    nir = _crop_to_bbox(b08_path, min_lon, min_lat, max_lon, max_lat)

    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = (nir - red) / (nir + red)

    # Clamp to valid range [-1, 1]
    ndvi = np.clip(ndvi, -1.0, 1.0)
    return ndvi


def compute_ndvi_diff(ndvi_before: np.ndarray, ndvi_after: np.ndarray) -> dict:
    """
    Compute pixel-wise NDVI difference and derive summary statistics.
    Returns dict with stats and the diff array.
    """
    diff = ndvi_after - ndvi_before

    valid = ~(np.isnan(diff))
    valid_pixels = int(np.sum(valid))

    if valid_pixels == 0:
        return {
            "mean_diff": 0.0,
            "std_diff": 0.0,
            "changed_pixel_pct": 0.0,
            "ndvi_before_mean": 0.0,
            "ndvi_after_mean": 0.0,
            "valid_pixels": 0,
            "diff_array": diff,
        }

    mean_diff = float(np.nanmean(diff))
    std_diff = float(np.nanstd(diff))
    ndvi_before_mean = float(np.nanmean(ndvi_before))
    ndvi_after_mean = float(np.nanmean(ndvi_after))

    # Pixels that changed by more than 0.1 NDVI units
    changed = np.sum(np.abs(diff[valid]) > 0.1)
    changed_pixel_pct = float(changed / valid_pixels * 100)

    return {
        "mean_diff": round(mean_diff, 4),
        "std_diff": round(std_diff, 4),
        "changed_pixel_pct": round(changed_pixel_pct, 2),
        "ndvi_before_mean": round(ndvi_before_mean, 4),
        "ndvi_after_mean": round(ndvi_after_mean, 4),
        "valid_pixels": valid_pixels,
        "diff_array": diff,
    }


def _resize_if_needed(img: Image.Image) -> Image.Image:
    w, h = img.size
    if max(w, h) > MAX_IMAGE_SIZE:
        scale = MAX_IMAGE_SIZE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def render_rgb_b64(
    b04_path: Path,
    b08_path: Path,
    b02_path: Path,
    b03_path: Path,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
) -> str:
    """Render true-colour RGB (B04=R, B03=G, B02=B) as base64 PNG."""
    red = _crop_to_bbox(b04_path, min_lon, min_lat, max_lon, max_lat)
    green = _crop_to_bbox(b03_path, min_lon, min_lat, max_lon, max_lat)
    blue = _crop_to_bbox(b02_path, min_lon, min_lat, max_lon, max_lat)

    def _norm(band: np.ndarray) -> np.ndarray:
        # Sentinel-2 L2A reflectance values 0-10000; stretch to 0-255
        band = np.nan_to_num(band, nan=0.0)
        p2, p98 = np.percentile(band[band > 0], [2, 98]) if np.any(band > 0) else (0, 1)
        band = np.clip((band - p2) / max(p98 - p2, 1) * 255, 0, 255).astype(np.uint8)
        return band

    rgb = np.stack([_norm(red), _norm(green), _norm(blue)], axis=-1)
    img = Image.fromarray(rgb, mode="RGB")
    img = _resize_if_needed(img)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def render_diff_image_b64(diff_array: np.ndarray) -> str:
    """
    Render NDVI diff as a colormap PNG:
      - Blue: vegetation gain (positive diff)
      - Red:  vegetation loss (negative diff)
      - White/grey: no change
    Returns base64 PNG.
    """
    h, w = diff_array.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)

    valid = ~np.isnan(diff_array)
    rgb[~valid] = [128, 128, 128]  # grey for nodata

    # Normalize to [-1, 1] for colouring
    gain = np.where(valid & (diff_array > 0), diff_array, 0)
    loss = np.where(valid & (diff_array < 0), -diff_array, 0)

    max_val = max(
        float(np.nanmax(np.abs(diff_array[valid]))) if valid.any() else 1.0, 0.01
    )
    gain_intensity = np.clip(gain / max_val * 255, 0, 255).astype(np.uint8)
    loss_intensity = np.clip(loss / max_val * 255, 0, 255).astype(np.uint8)

    # Blue channel for gain, Red channel for loss
    rgb[:, :, 2] = np.where(valid & (diff_array > 0), gain_intensity, 0)
    rgb[:, :, 0] = np.where(valid & (diff_array < 0), loss_intensity, 0)
    # Green base for no-significant-change areas
    no_change = valid & (np.abs(diff_array) <= 0.05)
    rgb[no_change] = [200, 200, 200]

    img = Image.fromarray(rgb, mode="RGB")
    img = _resize_if_needed(img)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
