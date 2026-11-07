"""Settings read environment variables only, never local secret files."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TrustSettings:
    database_url: str
    jwt_secret: str
    approval_secret: str
    demo_auth_enabled: bool = False
    simulation_mode: bool = True

    def __post_init__(self):
        if not self.database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("TRUST_DATABASE_URL must select PostgreSQL")
        for value in (self.jwt_secret, self.approval_secret):
            if len(value.encode()) < 32:
                raise ValueError("Trust signing keys must contain at least 32 bytes")
        if self.jwt_secret == self.approval_secret:
            raise ValueError("Authentication and approval keys must be different")
        if not self.simulation_mode:
            raise ValueError("This module permits synthetic simulation only")

    @classmethod
    def from_environment(cls):
        return cls(
            database_url=os.environ.get("TRUST_DATABASE_URL", ""),
            jwt_secret=os.environ.get("TRUST_JWT_SECRET", ""),
            approval_secret=os.environ.get("TRUST_APPROVAL_SECRET", ""),
            demo_auth_enabled=os.environ.get("TRUST_DEMO_AUTH_ENABLED") == "true",
            simulation_mode=os.environ.get("SIMULATION_MODE", "true") == "true",
        )
