import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.services.db import init_pool, close_pool
from app.api.routes import areas, analyses, alerts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    Path(settings.temp_dir).mkdir(parents=True, exist_ok=True)
    logger.info(f"Temp dir: {settings.temp_dir}")
    await init_pool()
    yield
    await close_pool()


app = FastAPI(
    title="Sentinel Watch",
    description="Satellite change detection with Sentinel-2 and Claude Vision",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(areas.router, prefix="/api/v1")
app.include_router(analyses.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "sentinel-watch"}


# Serve frontend — mount last so API routes take precedence
_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
