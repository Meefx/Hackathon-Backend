"""Both published branch heads upgrade without losing their populated data."""

import pytest
from alembic import command
from psycopg import sql

from app.core.clock import FrozenClock
from app.db.connection import REQUIRED_REVISION, check_ready, connect, create_pool
from app.modules.knowledge_sources.service import SourceService
from app.seed import seed
from tests.conftest import migration_config
from tests.test_source_archive import prepared_source


def snapshot(connection, tables):
    return {
        (schema, table): connection.execute(
            sql.SQL(
                "SELECT row_to_json(t)::text AS row FROM {} t ORDER BY row_to_json(t)::text"
            ).format(sql.Identifier(schema, table))
        ).fetchall()
        for schema, table in tables
    }


@pytest.mark.parametrize(
    "prior_head",
    [
        "0003_source_archive",
        "0005_knowledge_collection",
        "0006_source_collection_merge",
        "0008_whatsapp_collection",
    ],
)
def test_populated_published_heads_upgrade_intact(
    empty_database_url, trust_now, trust_actor, prior_head
):
    config = migration_config(empty_database_url)
    command.upgrade(config, prior_head)
    if prior_head == "0003_source_archive":
        with create_pool(empty_database_url) as pool:
            service = SourceService(None, pool, FrozenClock(trust_now))
            result = service.stage(trust_actor(), prepared_source(trust_now))
            assert result["draft"] == 1
    else:
        seed()
    with connect(empty_database_url) as connection:
        rows = connection.execute(
            "SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('public','trust') "
            "AND tablename<>'alembic_version' ORDER BY 1,2"
        ).fetchall()
        tables = [(row["schemaname"], row["tablename"]) for row in rows]
        before = snapshot(connection, tables)
        assert any(before.values())
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with connect(empty_database_url) as connection:
        assert snapshot(connection, tables) == before
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [
            {"version_num": REQUIRED_REVISION}
        ]
        connection.execute("SELECT account_id FROM collection_authorities LIMIT 0")
        connection.execute("SELECT artifact_sha256 FROM trust.source_records LIMIT 0")
    assert check_ready(empty_database_url)
