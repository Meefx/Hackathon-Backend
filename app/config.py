from functools import lru_cache

from psycopg.conninfo import conninfo_to_dict
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "CollectFlow API"
    database_url: str = "postgresql://collectflow:local-demo-password@127.0.0.1:5432/collectflow"
    cors_origins: list[str] = ["http://localhost:3000"]

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        # Also accepts libpq conninfo, used for isolated PostgreSQL test schemas.
        conninfo_to_dict(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
