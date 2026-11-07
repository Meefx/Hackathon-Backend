"""PostgreSQL connections own a transaction and close it on every exit path."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings

REQUIRED_REVISION = "0003_review_holds"


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection]:
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
        return version is not None and version["version_num"] == REQUIRED_REVISION
