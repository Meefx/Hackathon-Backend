from concurrent.futures import ThreadPoolExecutor
from time import monotonic

import psycopg
import pytest

from app.config import get_settings
from app.db.connection import connect
from app.seed import seed


def test_runtime_database_deadlines_preserve_schema_and_cleanup_locks(database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_STATEMENT_TIMEOUT_MS", "250")
    monkeypatch.setenv("DATABASE_LOCK_TIMEOUT_MS", "100")
    get_settings.cache_clear()
    seed()
    with connect(database_url) as connection:
        assert (
            connection.execute("SHOW statement_timeout").fetchone()["statement_timeout"] == "250ms"
        )
        assert connection.execute("SHOW lock_timeout").fetchone()["lock_timeout"] == "100ms"
        assert connection.execute("SELECT count(*) AS n FROM accounts").fetchone()["n"] == 8
    started = monotonic()
    with pytest.raises(psycopg.errors.QueryCanceled), connect(database_url) as connection:
        connection.execute("SELECT pg_sleep(2)")
    assert monotonic() - started < 1.5
    with connect(database_url) as holder:
        holder.execute("SELECT id FROM accounts WHERE id='CF-1001' FOR UPDATE")

        def blocked_write():
            started = monotonic()
            with pytest.raises(psycopg.errors.LockNotAvailable), connect(database_url) as blocked:
                blocked.execute(
                    "UPDATE accounts SET assigned_to='should-not-persist' WHERE id='CF-1001'"
                )
            return monotonic() - started

        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(blocked_write).result(timeout=2) < 1.5
    # Both failed transactions closed and their locks/writes were cleaned up.
    with connect(database_url) as connection:
        connection.execute("SELECT id FROM accounts WHERE id='CF-1001' FOR UPDATE NOWAIT")
        assert (
            connection.execute("SELECT assigned_to FROM accounts WHERE id='CF-1001'").fetchone()[
                "assigned_to"
            ]
            != "should-not-persist"
        )
