"""Real PostgreSQL tests, each in a fresh schema on an explicitly named test DB."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[1]


def migration_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


@pytest.fixture
def database_url(monkeypatch):
    base_url = os.environ.get("TEST_DATABASE_URL")
    if not base_url or not conninfo_to_dict(base_url).get("dbname", "").endswith("_test"):
        pytest.fail("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test")
    schema = f"test_{uuid4().hex}"
    with psycopg.connect(base_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    isolated_url = make_conninfo(base_url, options=f"-csearch_path={schema}")
    monkeypatch.setenv("DATABASE_URL", isolated_url)
    get_settings.cache_clear()
    try:
        command.upgrade(migration_config(), "head")
        yield isolated_url
    finally:
        get_settings.cache_clear()
        with psycopg.connect(base_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
