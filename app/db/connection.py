"""PostgreSQL connections own a transaction and close it on every exit path."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

REQUIRED_REVISION = "0008_whatsapp_collection"


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    settings = get_settings()
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5) as connection:
        # Transaction-local bounds preserve libpq options, including isolated test search_path.
        connection.execute(
            "SELECT set_config('statement_timeout', %s, true), "
            "set_config('lock_timeout', %s, true)",
            (str(settings.database_statement_timeout_ms), str(settings.database_lock_timeout_ms)),
        )
        yield connection


def check_ready(database_url: str) -> bool:
    with connect(database_url) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        connection.execute(
            "SELECT id, borrower_name, loan_reference, outstanding_amount, due_date, assigned_to "
            "FROM accounts LIMIT 0"
        )
        connection.execute("SELECT id FROM collection_runs LIMIT 0")
        connection.execute("SELECT id FROM collection_holds LIMIT 0")
        connection.execute("SELECT account_id FROM collection_authorities LIMIT 0")
        connection.execute("SELECT seq, hash, prev_hash FROM trust.audit_events LIMIT 0")
        connection.execute("SELECT embedding::public.vector FROM trust.knowledge_chunks LIMIT 0")
        connection.execute("SELECT status, policy_version FROM trust.attempts LIMIT 0")
        return version is not None and version["version_num"] == REQUIRED_REVISION


def create_pool(database_url: str) -> ConnectionPool[psycopg.Connection[dict[str, Any]]]:
    """Bounded sync pool; callers explicitly own its open/close lifecycle."""
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=5,
        timeout=3,
        max_waiting=20,
        kwargs={
            "row_factory": dict_row,
            "connect_timeout": 3,
            "options": "-c statement_timeout=5000 -c lock_timeout=3000",
        },
        open=False,
    )
