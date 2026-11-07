"""Real PostgreSQL tests use a fresh disposable database for every test.

A fixed trust schema cannot be isolated by search_path. The configured base DB
must end in _test and its role needs CREATEDB. Only randomly named databases
created by this fixture are dropped; the configured base is never mutated.
"""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL

from app.config import get_settings
from app.trust.security import Actor

ROOT = Path(__file__).resolve().parents[1]


def migration_config(database_url: str | None = None) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    if database_url is not None:
        config.attributes["database_url"] = database_url
    return config


def require_test_database_url(value: str | None) -> str:
    if not value or not conninfo_to_dict(value).get("dbname", "").endswith("_test"):
        raise ValueError("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test")
    return value


@contextmanager
def disposable_database(base_url: str) -> Iterator[str]:
    require_test_database_url(base_url)
    name = f"test_{uuid4().hex}_test"
    parameters = conninfo_to_dict(base_url)
    parameters.pop("dbname", None)
    parameters.pop("options", None)
    isolated_url = URL.create(
        "postgresql",
        username=parameters.pop("user", None),
        password=parameters.pop("password", None),
        host=parameters.pop("host", None),
        port=int(parameters.pop("port")) if parameters.get("port") else None,
        database=name,
        query=parameters,
    ).render_as_string(hide_password=False)
    with psycopg.connect(base_url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name))
        )
    try:
        yield isolated_url
    finally:
        with psycopg.connect(base_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def empty_database_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    try:
        base_url = require_test_database_url(
            os.environ.get("TEST_DATABASE_URL") or os.environ.get("TRUST_TEST_DATABASE_URL")
        )
    except (ValueError, psycopg.ProgrammingError):
        pytest.fail("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test")
    with disposable_database(base_url) as isolated_url:
        monkeypatch.setenv("DATABASE_URL", isolated_url)
        get_settings.cache_clear()
        try:
            yield isolated_url
        finally:
            get_settings.cache_clear()


@pytest.fixture
def database_url(empty_database_url: str) -> str:
    command.upgrade(migration_config(empty_database_url), "head")
    return empty_database_url


@pytest.fixture
def trust_dsn(database_url: str) -> str:
    return database_url


@pytest.fixture
def trust_now() -> datetime:
    return datetime(2026, 11, 9, 3, tzinfo=UTC)


@pytest.fixture
def trust_actor() -> Callable[..., Actor]:
    def actor(role: str = "knowledge", principal: str = "cf", product: str = "spaylater") -> Actor:
        return Actor(subject=f"demo-{role}", role=role, principal=principal, product=product)

    return actor


@pytest.fixture
def trust_conn(trust_dsn: str) -> Iterator[psycopg.Connection]:
    from app.trust.db import connection

    with connection(trust_dsn) as conn:
        yield conn
        conn.rollback()
