"""
Copernicus Data Space Ecosystem (CDSE) service.
Handles OAuth2 token caching, OData product search, and band download.
"""
import asyncio
import io
import logging
import os
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_CDSE_TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
_CDSE_ODATA_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1"
_CDSE_ZIPPER_URL = "https://zipper.dataspace.copernicus.eu/odata/v1"

# Bands we need: B04=Red, B08=NIR, B02=Blue, B03=Green (all at 10m resolution)
BANDS_NEEDED = ["B04", "B08", "B02", "B03"]


class _TokenCache:
    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    def is_valid(self) -> bool:
        # Refresh 30s before expiry
        return self._token is not None and time.monotonic() < self._expires_at - 30

    def set(self, token: str, expires_in: int) -> None:
        self._token = token
        self._expires_at = time.monotonic() + expires_in

    @property
    def token(self) -> Optional[str]:
        return self._token


_token_cache = _TokenCache()


class CopernicusService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._client = httpx.AsyncClient(timeout=300.0)

    async def _get_token(self) -> str:
        if _token_cache.is_valid():
            return _token_cache.token  # type: ignore[return-value]

        logger.info("Fetching new CDSE OAuth2 token")
        resp = await self._client.post(
            _CDSE_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._settings.cdse_client_id,
                "client_secret": self._settings.cdse_client_secret,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        _token_cache.set(payload["access_token"], payload.get("expires_in", 600))
        return _token_cache.token  # type: ignore[return-value]

    async def find_product(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        target_date: date,
        cloud_max: int = 20,
        window_days: int = 3,
    ) -> dict:
        """
        Search for the best Sentinel-2 L2A product within ±window_days of target_date
        with cloud cover < cloud_max%. Returns the product with lowest cloud cover.
        """
        date_from = target_date - timedelta(days=window_days)
        date_to = target_date + timedelta(days=window_days)

        wkt_polygon = (
            f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},"
            f"{max_lon} {max_lat},{min_lon} {max_lat},{min_lon} {min_lat}))"
        )

        filter_expr = (
            f"Collection/Name eq 'SENTINEL-2' "
            f"and Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' "
            f"and att/OData.CSC.StringAttribute/Value eq 'S2MSI2A') "
            f"and ContentDate/Start gt {date_from.isoformat()}T00:00:00.000Z "
            f"and ContentDate/Start lt {date_to.isoformat()}T23:59:59.000Z "
            f"and OData.CSC.Intersects(area=geography'SRID=4326;{wkt_polygon}') "
            f"and Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' "
            f"and att/OData.CSC.DoubleAttribute/Value lt {cloud_max})"
        )

        params = {
            "$filter": filter_expr,
            "$orderby": "Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute/Value) asc",
            "$top": "5",
            "$expand": "Attributes",
        }

        token = await self._get_token()
        resp = await self._client.get(
            f"{_CDSE_ODATA_URL}/Products",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("value"):
            raise ValueError(
                f"No Sentinel-2 L2A products found near {target_date} with cloud < {cloud_max}%"
            )

        product = data["value"][0]
        logger.info(f"Found product: {product['Name']} (id={product['Id']})")
        return product

    async def download_bands(
        self,
        product_id: str,
        product_name: str,
        out_dir: Path,
    ) -> dict[str, Path]:
        """
        Download the required bands from CDSE zipper service.
        Returns a mapping {band_name: local_path}, e.g. {"B04": Path(...)}
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        token = await self._get_token()

        # Build node filter to download only 10m band files (saves bandwidth vs full tile)
        # CDSE zipper supports selecting specific nodes
        node_filter = ",".join(
            f"{product_name}.SAFE/GRANULE/*/IMG_DATA/R10m/*_{b}_10m.jp2"
            for b in BANDS_NEEDED
        )

        url = f"{_CDSE_ZIPPER_URL}/Products({product_id})/$value"

        logger.info(f"Downloading bands for product {product_id}")
        zip_bytes = io.BytesIO()

        async with self._client.stream(
            "GET",
            url,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                zip_bytes.write(chunk)

        zip_bytes.seek(0)
        return await asyncio.to_thread(
            self._extract_bands, zip_bytes, out_dir, product_name
        )

    def _extract_bands(
        self, zip_bytes: io.BytesIO, out_dir: Path, product_name: str
    ) -> dict[str, Path]:
        result: dict[str, Path] = {}
        with zipfile.ZipFile(zip_bytes) as zf:
            names = zf.namelist()
            for band in BANDS_NEEDED:
                # Pattern: *_B04_10m.jp2 anywhere in the zip
                matches = [
                    n for n in names
                    if f"_{band}_10m.jp2" in n and "R10m" in n
                ]
                if not matches:
                    # Fallback: any file with band name
                    matches = [n for n in names if f"_{band}" in n and n.endswith(".jp2")]

                if not matches:
                    logger.warning(f"Band {band} not found in zip")
                    continue

                src = matches[0]
                dest = out_dir / f"{band}.jp2"
                with zf.open(src) as f_in, open(dest, "wb") as f_out:
                    f_out.write(f_in.read())
                result[band] = dest
                logger.info(f"Extracted {band} → {dest}")

        return result

    async def aclose(self) -> None:
        await self._client.aclose()
