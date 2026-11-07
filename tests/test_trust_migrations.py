"""Migration ownership, row preservation and fixed-schema test isolation."""

import psycopg
import pytest
from alembic import command
from sqlalchemy.exc import ProgrammingError

from app.db.connection import REQUIRED_REVISION, check_ready, connect
from app.trust.db import initialize
from tests.conftest import disposable_database, migration_config, require_test_database_url


def test_empty_database_to_head_is_ready_and_vector_is_available(empty_database_url):
    command.upgrade(migration_config(empty_database_url), "head")
    assert check_ready(empty_database_url)
    with connect(empty_database_url) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == {
            "version_num": REQUIRED_REVISION
        }
        assert (
            connection.execute(
                "SELECT '[1,0]'::public.vector <=> '[0,1]'::public.vector"
            ).fetchone()["?column?"]
            == 1
        )
        assert (
            connection.execute("SELECT count(*) FROM trust.audit_events").fetchone()["count"] == 0
        )


def test_accounts_baseline_upgrade_preserves_every_field(empty_database_url):
    config = migration_config(empty_database_url)
    command.upgrade(config, "0001_accounts")
    with connect(empty_database_url) as connection:
        connection.execute(
            "INSERT INTO accounts VALUES ('CF-MIGRATE', 'Synthetic', 'LN-MIGRATE', "
            "5000000000, '2026-11-07', 'Demo Agent')"
        )
        before = connection.execute("SELECT * FROM accounts").fetchall()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with connect(empty_database_url) as connection:
        assert connection.execute("SELECT * FROM accounts").fetchall() == before
    assert check_ready(empty_database_url)


def test_runtime_initialize_never_creates_schema(empty_database_url):
    with pytest.raises(psycopg.errors.UndefinedTable):
        initialize(empty_database_url)
    with connect(empty_database_url) as connection:
        assert (
            connection.execute("SELECT to_regnamespace('trust') AS schema").fetchone()["schema"]
            is None
        )
        assert (
            connection.execute("SELECT to_regclass('accounts') AS table_name").fetchone()[
                "table_name"
            ]
            is None
        )


def test_unmanaged_trust_schema_is_rejected_and_preserved(empty_database_url):
    config = migration_config(empty_database_url)
    command.upgrade(config, "0001_accounts")
    with connect(empty_database_url) as connection:
        connection.execute("CREATE SCHEMA trust")
        connection.execute("CREATE TABLE trust.marker (value text)")
        connection.execute("INSERT INTO trust.marker VALUES ('synthetic preserved marker')")
    with pytest.raises(ProgrammingError):
        command.upgrade(config, "head")
    with connect(empty_database_url) as connection:
        assert connection.execute("SELECT * FROM trust.marker").fetchone() == {
            "value": "synthetic preserved marker"
        }
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == {
            "version_num": "0001_accounts"
        }


def test_populated_trust_downgrade_refuses_to_destroy_audit(database_url):
    with connect(database_url) as connection:
        connection.execute(
            "INSERT INTO trust.audit_events VALUES (1, gen_random_uuid(), "
            "'2026-11-07T03:00:00Z', 'demo', 'qa', 'cf', 'spaylater', "
            "'migration_test', '{}'::jsonb, repeat('0', 64), repeat('1', 64))"
        )
        before = connection.execute("SELECT * FROM trust.audit_events").fetchall()
    with pytest.raises(RuntimeError, match="Cannot downgrade populated trust data"):
        command.downgrade(migration_config(database_url), "0001_accounts")
    with connect(database_url) as connection:
        assert connection.execute("SELECT * FROM trust.audit_events").fetchall() == before
    assert check_ready(database_url)


def test_readiness_rejects_missing_trust_table(database_url):
    with connect(database_url) as connection:
        connection.execute("DROP TABLE trust.attempts CASCADE")
    with pytest.raises(psycopg.errors.UndefinedTable):
        check_ready(database_url)


@pytest.mark.parametrize("value", [None, "", "postgresql://localhost/demo", "dbname=production"])
def test_fixture_refuses_non_test_database(value):
    with pytest.raises(ValueError, match="dedicated PostgreSQL database"):
        require_test_database_url(value)


def test_disposable_databases_isolate_fixed_trust_schema(database_url):
    with disposable_database(database_url) as second_url:
        command.upgrade(migration_config(second_url), "head")
        with connect(database_url) as first, connect(second_url) as second:
            first.execute(
                "INSERT INTO accounts VALUES ('CF-FIRST', 'Synthetic', 'LN-FIRST', "
                "1, '2026-11-07', 'Demo Agent')"
            )
            assert second.execute("SELECT count(*) FROM accounts").fetchone()["count"] == 0
            first.execute("CREATE TABLE trust.isolation_marker (value integer)")
            assert (
                second.execute("SELECT to_regclass('trust.isolation_marker') AS marker").fetchone()[
                    "marker"
                ]
                is None
            )
