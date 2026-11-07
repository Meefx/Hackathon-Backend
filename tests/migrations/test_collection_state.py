from datetime import UTC, date, datetime

from alembic import command

from app.db.connection import connect
from app.modules.compliance.service import evaluate_contact_policy
from tests.conftest import migration_config


def test_upgrade_preserves_existing_portfolio_and_defaults_fail_closed(database_url):
    command.downgrade(migration_config(), "0001_accounts")
    with connect(database_url) as connection:
        for index in range(8):
            connection.execute(
                "INSERT INTO accounts VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    f"old-{index}",
                    "Synthetic Borrower",
                    f"old-loan-{index}",
                    5_000_000_000 + index,
                    date(2026, 10, 1),
                    "Collector",
                ),
            )
        before = connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    command.upgrade(migration_config(), "head")
    with connect(database_url) as connection:
        assert connection.execute("SELECT * FROM accounts ORDER BY id").fetchall() == before
        decision = evaluate_contact_policy(
            connection, "old-0", datetime(2026, 11, 7, 3, tzinfo=UTC)
        )
        assert not decision["allowed"]
        assert {"policy_missing", "recipient_unverified", "account_ineligible"} <= set(
            decision["reason_codes"]
        )
