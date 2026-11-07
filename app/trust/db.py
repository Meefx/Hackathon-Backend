"""Compatibility facade over shared PostgreSQL lifecycle. Never performs DDL."""

from app.db.connection import check_ready
from app.db.connection import connect as connection  # noqa: F401
from app.db.connection import create_pool as pool  # noqa: F401


def initialize(dsn: str) -> None:
    """Legacy call now checks readiness; migrate explicitly before startup."""
    if not check_ready(dsn):
        raise RuntimeError("Database is not ready; run alembic upgrade head")
