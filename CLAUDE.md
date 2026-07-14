# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Sentinel Watch: a satellite change-detection service. It pulls before/after Sentinel-2 imagery
from Copernicus Data Space Ecosystem (CDSE), computes NDVI (vegetation index) differences, renders
the result as images, and sends those images + stats to Claude Vision for classification
(deforestation, flood, fire, urban growth, etc.), storing the results as alerts in a local
Postgres database. Backend is FastAPI + Python; frontend is a static Leaflet map page served by
the same app.

## Commands

```bash
make install       # uv sync
make db-up         # docker compose up -d db — starts local Postgres, auto-applies sql/schema.sql
make db-down       # docker compose down
make db-logs       # tail the Postgres container logs
make dev           # run API with reload on :8000 (requires db-up first)
make lint          # ruff check app/
make format        # ruff format app/
make test-download # scripts/test_download.py — standalone integration test that hits
                    # real CDSE + does NDVI/rendering, no Claude/DB calls
make health        # curl /health
```

No pytest-based test suite exists — `scripts/test_download.py` is the only test, and it makes
real network calls to CDSE (requires valid `CDSE_CLIENT_ID`/`CDSE_CLIENT_SECRET` in `.env`).

Config is loaded via `pydantic-settings` from a `.env` file (see `.env.example` for required
keys: `DATABASE_URL`, CDSE OAuth2 client credentials, Anthropic API key). The default
`DATABASE_URL` matches the docker-compose Postgres service, so no `.env` override is needed for
local dev once `make db-up` has run.

## Architecture

**Pipeline** (`app/api/routes/analyses.py::_run_pipeline`, run as a FastAPI `BackgroundTasks` job
after `POST /api/v1/analyses` returns 202):

1. Look up the `areas` row (bbox) and create an `analyses` row (`status=processing`).
2. `CopernicusService.find_product` — OData search against CDSE catalogue for the best Sentinel-2
   L2A product within ±3 days of each target date, filtered by cloud cover and bbox intersection.
3. `CopernicusService.download_bands` — streams the product zip from the CDSE zipper service and
   extracts only the four 10m bands needed (B04 red, B08 NIR, B02 blue, B03 green) into
   `TEMP_DIR/{analysis_id}/{before,after}/`.
4. `raster.compute_ndvi` / `compute_ndvi_diff` — crops bands to the area bbox (reprojecting WGS84
   → dataset CRS with rasterio), computes NDVI per date and the pixel-wise diff + summary stats.
   These are pure numpy/rasterio functions always invoked via `asyncio.to_thread` since they're
   CPU-bound and rasterio is sync.
5. `raster.render_rgb_b64` / `render_diff_image_b64` — renders true-colour composites and a
   red/blue NDVI-diff colormap as base64 PNGs, capped to `MAX_IMAGE_SIZE` (1024px, a Claude Vision
   constraint).
6. `ClaudeAnalyzer.analyze_change` — sends the 3 images to `claude-opus-4-6` with a tool-use loop
   (`app/services/claude_analyzer.py`). Uses three tools as a structured-output pattern:
   - `get_ndvi_stats` / `get_area_metadata` — data injection: Claude "calls" these and the app
     just returns pre-computed JSON already sitting in memory (not real external lookups).
   - `classify_change` — the only way Claude can submit its final answer; its `tool_use.input`
     is captured directly as the classification dict. Loop caps at 6 turns; if Claude never calls
     `classify_change`, a low-confidence fallback classification is used instead of failing.
   - Note: `analyses.py` has its own near-duplicate copy of this tool-use loop
     (`_run_claude_sync`), used because `ClaudeAnalyzer.analyze_change` is async but the pipeline
     needs to run the Anthropic SDK call in a thread pool. Keep both in sync if you change the
     prompt, tool schemas, or loop logic — or fix the duplication.
7. `alert_writer.save_alert` — inserts one row into `alerts`, including the full raw Claude
   response trace (`raw_claude_response`) for auditability.
8. `analyses` row updated to `status=done` (or `status=error` with truncated `error_message` on
   any exception — the whole pipeline is wrapped in one try/except).

**Data model** (`sql/schema.sql`, mirrored by `app/models/*.py` Pydantic models):
`areas` (monitored bboxes) → `analyses` (one per before/after run, tracks pipeline status) →
`alerts` (Claude's classification output, FK to both `analyses` and `areas`). Postgres access
goes through `app/services/db.py` — a process-wide `asyncpg.Pool` created in `main.py`'s lifespan
(`init_pool`/`close_pool`) and fetched via `get_pool()`. Routes and services write raw SQL
directly (no ORM/repository layer). Two things every query site has to handle manually:
- `id`/`*_id` columns are Postgres `UUID`; asyncpg decodes them to `uuid.UUID` objects, but the
  Pydantic models type them as `str` — use `db.row_to_dict(row)` (not bare `dict(row)`) to
  stringify UUID columns before constructing a model.
- `JSONB` columns (`ndvi_stats`, `raw_claude_response`) come back from asyncpg as JSON-encoded
  strings, not dicts — each route that reads them (`_analysis_dict` in `analyses.py`,
  `_alert_dict` in `alerts.py`) does `json.loads` on top of `row_to_dict`. Writes must
  `json.dumps(...)` the value before binding it as a query parameter.

**CDSE OAuth2**: token is cached process-wide in a module-level `_TokenCache` in
`copernicus.py` (not per-request), refreshed 30s before expiry.

**Frontend**: static HTML/JS/CSS in `frontend/`, mounted at `/` by `app/main.py` (after API routes
so `/api/v1/*` takes precedence). Leaflet map lets a user draw a bbox, which becomes an `areas`
row; `app.js` polls `GET /api/v1/analyses/{id}` for pipeline status.

**Constraint**: `areas.max_bbox_degrees` (default 1.0°) caps bbox size at creation time — larger
areas would span multiple Sentinel-2 tiles, unsupported in this MVP.

## Notes for changes here

- Any new Claude tool-use logic needs to be added to both `claude_analyzer.py` and the duplicated
  loop in `analyses.py::_run_claude_sync` (see above).
- `raster.py` functions assume `rasterio`-openable inputs and are synchronous by design — call
  them via `asyncio.to_thread`, don't make them `async def`.
- Local Postgres runs via `docker-compose.yml` (single `db` service, named volume for data,
  `sql/schema.sql` mounted into `/docker-entrypoint-initdb.d/` so it's only applied on first
  container init — if you change the schema, either `docker compose down -v` to recreate the
  volume or apply the change manually with `psql`/`docker exec`).
