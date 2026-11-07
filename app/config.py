from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Curated public-source downloader policy. Documents cannot extend this allowlist.
SOURCE_DOWNLOAD_HOSTS = frozenset(
    {
        "ojk.go.id",
        "www.ojk.go.id",
        "peraturan.go.id",
        "www.peraturan.go.id",
        "peraturan.bpk.go.id",
        "jdih.kemenkeu.go.id",
        "jdih.komdigi.go.id",
        "mkri.id",
        "www.mkri.id",
        "s.mkri.id",
        "kemenkopmk.go.id",
        "www.kemenkopmk.go.id",
        "lenteradana.co.id",
        "www.lenteradana.co.id",
    }
)


@dataclass(frozen=True)
class TrustSettings:
    """Validated immutable trust configuration; secret values are never repr'd."""

    database_url: str = field(repr=False)
    jwt_secret: str = field(repr=False)
    approval_secret: str = field(repr=False)
    demo_auth_enabled: bool = False
    simulation_mode: bool = True

    def __post_init__(self) -> None:
        try:
            valid_database = bool(conninfo_to_dict(self.database_url).get("dbname"))
        except (ProgrammingError, ValueError):
            valid_database = False
        if not valid_database:
            raise ValueError("Trust database must select PostgreSQL with an explicit database")
        for value in (self.jwt_secret, self.approval_secret):
            if len(value.encode()) < 32:
                raise ValueError("Trust signing keys must contain at least 32 bytes")
        if self.jwt_secret == self.approval_secret:
            raise ValueError("Authentication and approval keys must be different")
        if not self.simulation_mode:
            raise ValueError("This module permits synthetic simulation only")

    @classmethod
    def from_environment(cls) -> "TrustSettings":
        # Standalone launchers use process environment, never implicit dotenv files.
        return Settings.from_environment().trust_settings()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "CollectFlow API"
    database_url: str = "postgresql://collectflow:local-demo-password@127.0.0.1:5432/collectflow"
    database_statement_timeout_ms: int = Field(default=10000, ge=1, le=60000)
    database_lock_timeout_ms: int = Field(default=2000, ge=1, le=60000)
    cors_origins: list[str] = ["http://localhost:3000"]
    trust_enabled: bool = False
    trust_database_url: str = Field(default="", repr=False)
    trust_jwt_secret: str = Field(default="", repr=False)
    trust_approval_secret: str = Field(default="", repr=False)
    trust_demo_auth_enabled: bool = False
    simulation_mode: bool = True

    @classmethod
    def from_environment(cls) -> "Settings":
        # Supported pydantic-settings init override, absent from mypy's generated signature.
        return cls(_env_file=None)  # type: ignore[call-arg]

    def trust_settings(self) -> TrustSettings:
        return TrustSettings(
            database_url=self.trust_database_url or self.database_url,
            jwt_secret=self.trust_jwt_secret,
            approval_secret=self.trust_approval_secret,
            demo_auth_enabled=self.trust_demo_auth_enabled,
            simulation_mode=self.simulation_mode,
        )

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
