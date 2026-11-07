"""PostgreSQL connections own a transaction and close it on every exit path."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

REQUIRED_REVISION = "0001_accounts"


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5) as connection:
        yield connection


def check_ready(database_url: str) -> bool:
    with connect(database_url) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        connection.execute(
            "SELECT id, borrower_name, loan_reference, outstanding_amount, due_date, assigned_to "
            "FROM accounts LIMIT 0"
        )
        return version is not None and version["version_num"] == REQUIRED_REVISION
