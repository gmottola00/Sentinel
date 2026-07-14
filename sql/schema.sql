-- Sentinel Watch — Postgres Schema
-- Auto-applied on first boot by docker-compose (mounted into /docker-entrypoint-initdb.d/)

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─────────────────────────────────────────
-- areas: monitored geographic bounding boxes
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS areas (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    min_lon     DOUBLE PRECISION NOT NULL,
    min_lat     DOUBLE PRECISION NOT NULL,
    max_lon     DOUBLE PRECISION NOT NULL,
    max_lat     DOUBLE PRECISION NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- analyses: one row per (area, date_before, date_after) analysis run
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analyses (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    area_id             UUID NOT NULL REFERENCES areas(id) ON DELETE CASCADE,
    date_before         DATE NOT NULL,
    date_after          DATE NOT NULL,
    product_id_before   TEXT,           -- CDSE product UUID
    product_id_after    TEXT,
    ndvi_stats          JSONB,          -- {mean_diff, std_diff, changed_pixel_pct, ...}
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'processing', 'done', 'error')),
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS analyses_area_id_idx ON analyses(area_id);
CREATE INDEX IF NOT EXISTS analyses_status_idx  ON analyses(status);

-- ─────────────────────────────────────────
-- alerts: Claude's classification output
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    analysis_id         UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    area_id             UUID NOT NULL REFERENCES areas(id) ON DELETE CASCADE,
    severity            TEXT NOT NULL
                            CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    change_type         TEXT NOT NULL
                            CHECK (change_type IN (
                                'deforestation', 'flood', 'fire',
                                'urban_growth', 'agricultural', 'other', 'no_change'
                            )),
    confidence          DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    description         TEXT NOT NULL,
    raw_claude_response JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS alerts_area_id_idx     ON alerts(area_id);
CREATE INDEX IF NOT EXISTS alerts_analysis_id_idx ON alerts(analysis_id);
CREATE INDEX IF NOT EXISTS alerts_severity_idx    ON alerts(severity);
