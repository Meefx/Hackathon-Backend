from datetime import datetime
from functools import lru_cache

from psycopg.conninfo import conninfo_to_dict
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "CollectFlow API"
    database_url: str = "postgresql://collectflow:local-demo-password@127.0.0.1:5432/collectflow"
    database_statement_timeout_ms: int = Field(default=10000, ge=1, le=60000)
    database_lock_timeout_ms: int = Field(default=2000, ge=1, le=60000)
    cors_origins: list[str] = ["http://localhost:3000"]

    demo_fixture_enabled: bool = True
    knowledge_mode: str = "demo_fixture"
    knowledge_service_factory: str = ""
    approval_hmac_secret: SecretStr = SecretStr("local-synthetic-demo-change-before-sharing")
    approval_ttl_seconds: int = 900
    worker_lease_seconds: int = 60
    worker_poll_seconds: float = 2
    worker_runtime_deadline_seconds: int = 300
    hermes_api_base_url: str = ""
    hermes_api_key: SecretStr = SecretStr("")
    hermes_profile: str = ""
    hermes_timeout_seconds: float = 20
    mcp_bearer_token: SecretStr = SecretStr("")
    mcp_token_expires_at: datetime | None = None
    mcp_principal_id: str = "hermes-agent"
    mcp_allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*"]
    mcp_allowed_origins: list[str] = ["http://127.0.0.1:*", "http://localhost:*"]

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        # Also accepts libpq conninfo, used for isolated PostgreSQL test schemas.
        conninfo_to_dict(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
