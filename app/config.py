from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Postgres
    database_url: str = "postgresql://sentinel:sentinel@localhost:5432/sentinel_watch"

    # Copernicus Data Space
    cdse_client_id: str
    cdse_client_secret: str

    # Anthropic
    anthropic_api_key: str

    # App
    temp_dir: str = "/tmp/sentinel-watch"
    log_level: str = "INFO"
    max_bbox_degrees: float = 1.0  # warn if bbox > this


@lru_cache
def get_settings() -> Settings:
    return Settings()
