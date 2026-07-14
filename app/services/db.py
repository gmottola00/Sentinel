"""Postgres connection pool (asyncpg)."""
import uuid

import asyncpg

from app.config import get_settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(get_settings().database_url)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_pool() at startup")
    return _pool


def row_to_dict(row: asyncpg.Record) -> dict:
    """Convert an asyncpg Record to a dict, stringifying UUID columns (id fields are `str` in our Pydantic models)."""
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in dict(row).items()}
