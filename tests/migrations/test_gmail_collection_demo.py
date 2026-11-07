from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from alembic import command

from app.config import Settings
from app.db.connection import REQUIRED_REVISION, check_ready, connect
from app.modules.email_demo.schemas import EmailDemoRequest
from app.modules.email_demo.service import start_demo
from app.seed import seed
from app.shared.caller import Caller
from tests.conftest import migration_config


def test_email_demo_upgrade_preserves_portfolio_and_existing_state(empty_database_url, monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    config = migration_config(empty_database_url)
    command.upgrade(config, "0009_collection_source_merge")
    seed(date(2026, 11, 7))
    with connect(empty_database_url) as connection:
        accounts = connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        authorities = connection.execute(
            "SELECT * FROM collection_authorities ORDER BY account_id"
        ).fetchall()
    command.upgrade(config, "head")
    assert check_ready(empty_database_url)
    with connect(empty_database_url) as connection:
        assert connection.execute("SELECT * FROM accounts ORDER BY id").fetchall() == accounts
        assert (
            connection.execute(
                "SELECT * FROM collection_authorities ORDER BY account_id"
            ).fetchall()
            == authorities
        )
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"]
            == REQUIRED_REVISION
        )
        assert not connection.execute("SELECT 1 FROM collection_email_demos").fetchone()
    command.downgrade(config, "0009_collection_source_merge")
    with connect(empty_database_url) as connection:
        assert connection.execute("SELECT * FROM accounts ORDER BY id").fetchall() == accounts
    command.upgrade(config, "head")
    settings = Settings(
        _env_file=None,
        database_url=empty_database_url,
        gmail_client_id="synthetic-client",
        gmail_client_secret="synthetic-secret",
        gmail_refresh_token="synthetic-refresh",
        gmail_sender_email="sender@example.test",
        gmail_test_recipient="team@example.test",
    )
    demo = start_demo(
        settings,
        EmailDemoRequest(idempotency_key=uuid4()),
        Caller(),
        datetime(2026, 11, 7, 3, tzinfo=UTC),
    )
    with pytest.raises(RuntimeError, match="populated Gmail"):
        command.downgrade(config, "0009_collection_source_merge")
    assert check_ready(empty_database_url)
    with connect(empty_database_url) as connection:
        assert connection.execute(
            "SELECT 1 FROM collection_email_demos WHERE id=%s", (demo["id"],)
        ).fetchone()
