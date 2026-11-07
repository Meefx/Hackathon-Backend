"""PostgreSQL only. No silent SQLite or in-memory fallback."""

from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


@contextmanager
def connection(dsn: str):
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3) as conn:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        conn.execute("SET LOCAL lock_timeout = '3s'")
        yield conn


def initialize(dsn: str) -> None:
    directory = Path(__file__).parent
    with connection(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(71107001)")
        for filename in ("schema.sql", "runtime_schema.sql"):
            conn.execute((directory / filename).read_text())


def pool(dsn: str) -> ConnectionPool:
    return ConnectionPool(
        dsn,
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
