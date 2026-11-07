from datetime import UTC, date, datetime

import psycopg
import pytest
from alembic import command

from app.db.connection import REQUIRED_REVISION, check_ready, connect
from app.seed import seed
from tests.conftest import migration_config


def test_daily_collection_upgrade_preserves_portfolio_and_unique_admission(empty_database_url):
    config = migration_config(empty_database_url)
    command.upgrade(config, "0005_knowledge_collection")
    seed(date(2026, 11, 7))
    with connect(empty_database_url) as connection:
        before = connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    command.upgrade(config, "head")
    assert check_ready(empty_database_url)
    with connect(empty_database_url) as connection:
        assert connection.execute("SELECT * FROM accounts ORDER BY id").fetchall() == before
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"]
            == REQUIRED_REVISION
        )
        now = datetime(2026, 11, 7, 2, tzinfo=UTC)
        connection.execute(
            "INSERT INTO collection_schedule_items(id,business_date,account_id,state,created_at,updated_at) "
            "VALUES ('first','2026-11-07','CF-1001','deferred',%s,%s)",
            (now, now),
        )
    with pytest.raises(psycopg.errors.UniqueViolation), connect(empty_database_url) as connection:
        connection.execute(
            "INSERT INTO collection_schedule_items(id,business_date,account_id,state,created_at,updated_at) "
            "VALUES ('second','2026-11-07','CF-1001','queued',%s,%s)",
            (now, now),
        )
    command.downgrade(config, "0005_knowledge_collection")
    with connect(empty_database_url) as connection:
        assert connection.execute("SELECT * FROM accounts ORDER BY id").fetchall() == before
