from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import psycopg
import pytest
from alembic import command
from alembic.script import ScriptDirectory

from app.db.connection import REQUIRED_REVISION, connect
from app.modules.accounts import repository, service
from app.modules.dashboard.service import summarize
from app.seed import seed
from tests.conftest import migration_config

TODAY = date(2026, 11, 7)


def insert(database_url, days, amount=1000):
    with connect(database_url) as connection:
        connection.execute("INSERT INTO accounts VALUES (%s, %s, %s, %s, %s, %s)", (
            f"CF-{days}", "Demo Borrower", f"LN-{days}", amount,
            TODAY - timedelta(days=days), "Demo Agent",
        ))


@pytest.mark.parametrize("days,status", [
    (-1, "current"), (0, "current"), (1, "early"), (30, "early"),
    (31, "late"), (90, "late"), (91, "critical"),
])
def test_aging_boundaries(database_url, days, status):
    insert(database_url, days)
    account = service.get_account(database_url, f"CF-{days}", TODAY)
    assert account["status"] == status
    assert account["days_past_due"] == max(0, days)


def test_summary_and_parameterized_lookup(database_url):
    insert(database_url, -3, 1000)
    insert(database_url, 5, 2000)
    insert(database_url, 95, 3000)
    summary = summarize(service.list_accounts(database_url, TODAY))
    assert summary["total_outstanding"] == 6000
    assert summary["overdue_amount"] == 5000
    assert summary["overdue_accounts"] == 2
    assert sum(bucket["count"] for bucket in summary["buckets"]) == 3
    assert service.get_account(database_url, "' OR 1=1 --", TODAY) is None


def test_constraints_and_rollback(database_url):
    with pytest.raises(psycopg.errors.CheckViolation), connect(database_url) as connection:
        connection.execute("INSERT INTO accounts VALUES ('a', 'Demo', 'L1', 1, %s, 'Agent')",
                           (TODAY,))
        connection.execute("INSERT INTO accounts VALUES ('b', 'Demo', 'L2', -1, %s, 'Agent')",
                           (TODAY,))
    assert repository.list_accounts(database_url) == []
    insert(database_url, 1, 5_000_000_000)
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert(database_url, 1)
    with pytest.raises(psycopg.errors.UniqueViolation), connect(database_url) as connection:
        connection.execute("INSERT INTO accounts VALUES ('b', 'Demo', 'LN-1', 1, %s, 'Agent')",
                           (TODAY,))
    with pytest.raises(psycopg.errors.InvalidDatetimeFormat), connect(database_url) as connection:
        connection.execute("UPDATE accounts SET due_date = 'invalid'")


def test_seed_idempotent_concurrent_and_preserves_dates(database_url):
    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = list(executor.map(lambda _: seed(TODAY), range(2)))
    assert sum(counts) == 8
    before = repository.list_accounts(database_url)
    assert seed(TODAY + timedelta(days=10)) == 0
    assert repository.list_accounts(database_url) == before


def test_versioned_migration_preserves_rows_and_can_rebuild_empty_schema(database_url):
    config = migration_config()
    assert ScriptDirectory.from_config(config).get_current_head() == REQUIRED_REVISION
    insert(database_url, 1)
    before = repository.list_accounts(database_url)
    command.upgrade(config, "head")
    assert repository.list_accounts(database_url) == before
    with connect(database_url) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[
            "version_num"
        ] == REQUIRED_REVISION
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    assert repository.list_accounts(database_url) == []


def test_due_date_ties_are_ordered_by_id(database_url):
    with connect(database_url) as connection:
        for account_id in ("Z", "A"):
            connection.execute("INSERT INTO accounts VALUES (%s, 'Demo', %s, 1, %s, 'Agent')",
                               (account_id, f"LN-{account_id}", TODAY))
    assert [account["id"] for account in repository.list_accounts(database_url)] == ["A", "Z"]
