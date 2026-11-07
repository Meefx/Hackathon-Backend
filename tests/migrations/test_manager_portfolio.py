from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from alembic import command

from app.config import get_settings
from app.db.connection import check_ready, connect
from app.modules.manager.chat_schemas import ManagerChatRequest
from app.modules.manager.chat_service import ManagerChatService
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run
from app.seed import seed
from app.shared.caller import Caller
from tests.conftest import migration_config

NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)


def test_portfolio_upgrade_preserves_account_runs_and_allows_global_queue(empty_database_url):
    config = migration_config(empty_database_url)
    command.upgrade(config, "0006_daily_collection")
    seed(date(2026, 11, 7))
    old = start_run(
        get_settings(),
        "CF-1001",
        StartRunRequest(instruction="Existing", idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    with connect(empty_database_url) as connection:
        accounts = connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        run = connection.execute(
            "SELECT * FROM collection_runs WHERE id=%s", (old["run_id"],)
        ).fetchone()
    command.upgrade(config, "head")
    assert check_ready(empty_database_url)
    with connect(empty_database_url) as connection:
        assert connection.execute("SELECT * FROM accounts ORDER BY id").fetchall() == accounts
        assert (
            connection.execute(
                "SELECT * FROM collection_runs WHERE id=%s", (old["run_id"],)
            ).fetchone()
            == run
        )
    service = ManagerChatService(get_settings(), lambda: NOW)
    admitted = service.start_run(
        ManagerChatRequest(instruction="Portfolio", idempotency_key=uuid4()), Caller()
    )
    assert admitted["status"] == "queued"
    with pytest.raises(RuntimeError, match="archived"):
        command.downgrade(config, "0006_daily_collection")
    assert check_ready(empty_database_url)


def test_portfolio_downgrade_preserves_existing_account_session(empty_database_url):
    config = migration_config(empty_database_url)
    command.upgrade(config, "head")
    seed(date(2026, 11, 7))
    accepted = start_run(
        get_settings(),
        "CF-1001",
        StartRunRequest(instruction="Existing", idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    command.downgrade(config, "0006_daily_collection")
    with connect(empty_database_url) as connection:
        assert (
            connection.execute(
                "SELECT account_id FROM collection_runs WHERE id=%s", (accepted["run_id"],)
            ).fetchone()["account_id"]
            == "CF-1001"
        )
        assert (
            connection.execute(
                "SELECT account_id FROM manager_sessions WHERE id=%s",
                (accepted["manager_session_id"],),
            ).fetchone()["account_id"]
            == "CF-1001"
        )
