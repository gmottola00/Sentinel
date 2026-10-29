.PHONY: install dev test-download lint format health db-up db-down db-logs

install:
	uv sync

db-up:
	docker compose up -d db

db-down:
	docker compose down

db-logs:
	docker compose logs -f db

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test-download:
	uv run python scripts/test_download.py

lint:
	uv run ruff check app/

format:
	uv run ruff format app/

health:
	curl -s http://localhost:8000/health | python3 -m json.tool
