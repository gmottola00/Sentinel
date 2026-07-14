# 🛰 Sentinel Watch

Satellite change detection over any patch of Earth, using free Sentinel-2 imagery and Claude
Vision as the analyst.

Draw a bounding box on a map, pick two dates, and Sentinel Watch downloads the matching
Sentinel-2 scenes, computes the NDVI (vegetation index) difference between them, renders
before/after/diff imagery, and asks Claude to classify what changed — deforestation, fire,
flood, urban growth, agricultural change — with a severity score and a plain-English
explanation.

<p>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-async-009688">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
</p>

## Why

Satellite change detection normally means either paying for a commercial monitoring platform or
writing your own NDVI-threshold heuristics and hoping they generalize. This project explores a
third path: use a vision-capable LLM as the last mile of the pipeline. The quantitative work
(cloud filtering, band math, NDVI stats) is still done with real geospatial tooling — Claude
never sees raw satellite bytes or does the math itself. It's shown two rendered composites and a
diff map, handed the precomputed stats through tool calls, and asked to reason about what a
human analyst would reason about: does this look like clearing, burn scars, seasonal variation,
or nothing at all.

## How it works

```
 ┌──────────┐     ┌────────────────────┐     ┌───────────────┐     ┌──────────────────┐
 │  Leaflet  │────▶│  FastAPI  /analyses │────▶│  Copernicus    │────▶│  rasterio / numpy │
 │  bbox UI  │     │  (background task)  │     │  Data Space    │     │  NDVI + diff map  │
 └──────────┘     └────────────────────┘     └───────────────┘     └──────────────────┘
                                                                                │
                                                                                ▼
                    ┌────────────────┐     ┌─────────────────────────────────────────┐
                    │  Postgres       │◀────│  Claude Vision + tool use                │
                    │  areas/analyses/│     │  classify_change(type, severity, conf.)  │
                    │  alerts         │     └─────────────────────────────────────────┘
                    └────────────────┘
```

1. **Pick an area** — draw a bbox on the Leaflet map (max ~1° per side in this MVP, to stay
   within a single Sentinel-2 tile).
2. **Trigger an analysis** — `POST /api/v1/analyses` with a before/after date pair. The API
   returns immediately (`202`) and runs the pipeline as a background task.
3. **Fetch imagery** — the app authenticates with [Copernicus Data Space
   Ecosystem](https://dataspace.copernicus.eu/) (OAuth2 client credentials), finds the
   lowest-cloud-cover Sentinel-2 L2A product within ±3 days of each target date, and downloads
   only the four 10m bands it needs (Red, NIR, Green, Blue).
4. **Compute NDVI** — crops both dates to the bbox, computes NDVI, and derives a pixel-wise diff
   with summary statistics (mean/std change, % of pixels changed).
5. **Render for a vision model** — renders true-colour composites for both dates plus a
   red/blue NDVI-diff colormap as PNGs.
6. **Ask Claude** — sends the three images to Claude Vision with a small tool-use loop: Claude
   pulls the precomputed NDVI stats and area metadata through tool calls, then must call
   `classify_change` (its only way to answer) with a change type, severity, confidence, and
   description.
7. **Store the alert** — the classification, plus Claude's full raw response trace, is written
   to Postgres and surfaced back to the map UI.

## Tech stack

| Layer          | Choice                                                              |
|----------------|----------------------------------------------------------------------|
| API            | FastAPI (async), Pydantic v2                                        |
| Satellite data | Copernicus Data Space Ecosystem (OData catalogue + zipper download) |
| Raster/GIS     | rasterio, numpy, Shapely, Pillow                                    |
| AI             | Anthropic Claude (Vision + tool use)                                 |
| Database       | Postgres via `asyncpg` (raw SQL, no ORM)                             |
| Frontend       | Static HTML/CSS/JS + Leaflet, served by the same FastAPI app        |
| Dev tooling    | `uv` for dependency management, `ruff` for lint/format, Docker Compose for local Postgres |

## Getting started

### Prerequisites

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- Docker (for local Postgres)
- A [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu/) account with an OAuth2
  client (register for free)
- An [Anthropic API key](https://console.anthropic.com/)

### Setup

```bash
git clone https://github.com/<your-username>/sentinel-watch.git
cd sentinel-watch

cp .env.example .env
# edit .env: fill in CDSE_CLIENT_ID, CDSE_CLIENT_SECRET, ANTHROPIC_API_KEY
# DATABASE_URL already matches the docker-compose Postgres service below

make install   # uv sync
make db-up     # starts Postgres in Docker, applies sql/schema.sql automatically
make dev       # runs the API with reload on http://localhost:8000
```

Open `http://localhost:8000` — draw a bbox on the map (hold **Shift** + drag), pick two dates,
and submit. Poll status on the analysis card, or hit the API directly:

```bash
# create a monitored area
curl -X POST localhost:8000/api/v1/areas \
  -H "Content-Type: application/json" \
  -d '{"name":"Amazon Basin – Sector 4","min_lon":-55.5,"min_lat":-5.0,"max_lon":-55.0,"max_lat":-4.5}'

# trigger an analysis
curl -X POST localhost:8000/api/v1/analyses \
  -H "Content-Type: application/json" \
  -d '{"area_id":"<area-id>","date_before":"2022-07-01","date_after":"2023-07-01"}'

# poll for the result
curl localhost:8000/api/v1/analyses/<analysis-id>
curl localhost:8000/api/v1/alerts?area_id=<area-id>
```

## API reference

| Method | Path                     | Description                                           |
|--------|--------------------------|--------------------------------------------------------|
| POST   | `/api/v1/areas`          | Create a monitored bounding box                        |
| GET    | `/api/v1/areas`          | List areas                                              |
| GET    | `/api/v1/areas/{id}`     | Get one area                                            |
| POST   | `/api/v1/analyses`       | Trigger the full pipeline for an area + date pair (`202`, async) |
| GET    | `/api/v1/analyses`       | List analyses (optionally `?area_id=`)                  |
| GET    | `/api/v1/analyses/{id}`  | Get analysis status/result (`pending`/`processing`/`done`/`error`) |
| GET    | `/api/v1/alerts`         | List Claude's classifications (optionally `?area_id=`)  |
| GET    | `/api/v1/alerts/{id}`    | Get one alert, including Claude's full raw response trace |
| GET    | `/health`                | Liveness check                                          |

## Project structure

```
app/
├── api/routes/        # areas, analyses (pipeline orchestration), alerts
├── services/
│   ├── copernicus.py  # CDSE OAuth2 + product search + band download
│   ├── raster.py      # NDVI computation, diff maps, PNG rendering
│   ├── claude_analyzer.py  # Claude Vision + tool-use classification
│   ├── alert_writer.py     # persists classifications
│   └── db.py           # asyncpg pool
├── models/             # Pydantic request/response models
└── main.py              # FastAPI app, serves frontend/ as static files
frontend/                # Leaflet map UI (vanilla JS, no build step)
sql/schema.sql            # Postgres schema (areas → analyses → alerts)
docker-compose.yml         # local Postgres for development
```

See [`CLAUDE.md`](./CLAUDE.md) for a deeper architectural walkthrough (written for AI coding
assistants, but equally useful for humans jumping into the codebase).

## Limitations / roadmap

- Bounding boxes are capped at ~1° per side — larger areas span multiple Sentinel-2 tiles, which
  isn't handled yet (mosaicking is on the roadmap).
- No auth on the API — this is a single-user MVP; anyone with network access can create areas
  and trigger (billable) Claude/CDSE calls.
- No pytest suite yet — `scripts/test_download.py` is a standalone integration script for the
  CDSE + rasterio path.
- Alert classification is a single Claude pass with no human-in-the-loop review step.

## License

[MIT](./LICENSE)
