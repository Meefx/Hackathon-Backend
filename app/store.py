"""Small SQLite repository; all balances are whole Indonesian rupiah."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    borrower_name TEXT NOT NULL,
    loan_reference TEXT NOT NULL UNIQUE,
    outstanding_amount INTEGER NOT NULL CHECK (outstanding_amount >= 0),
    due_date TEXT NOT NULL,
    assigned_to TEXT NOT NULL
)
"""


@contextmanager
def connect(path: str) -> Iterator[sqlite3.Connection]:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path: str) -> None:
    with connect(path) as connection:
        connection.execute(SCHEMA)


def serialize_account(row: sqlite3.Row, today: date) -> dict:
    account = dict(row)
    days = max(0, (today - date.fromisoformat(account["due_date"])).days)
    status = "current" if days == 0 else "early" if days <= 30 else "late" if days <= 90 else "critical"
    return {**account, "days_past_due": days, "status": status}


def list_accounts(path: str, today: date) -> list[dict]:
    with connect(path) as connection:
        rows = connection.execute("SELECT * FROM accounts ORDER BY due_date, id").fetchall()
    return [serialize_account(row, today) for row in rows]


def get_account(path: str, account_id: str, today: date) -> dict | None:
    with connect(path) as connection:
        row = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return serialize_account(row, today) if row else None


def summarize(accounts: list[dict]) -> dict:
    overdue = [account for account in accounts if account["days_past_due"] > 0]
    return {
        "total_accounts": len(accounts),
        "total_outstanding": sum(account["outstanding_amount"] for account in accounts),
        "overdue_accounts": len(overdue),
        "overdue_amount": sum(account["outstanding_amount"] for account in overdue),
        "buckets": [
            {
                "status": status,
                "count": sum(account["status"] == status for account in accounts),
                "outstanding_amount": sum(
                    account["outstanding_amount"] for account in accounts
                    if account["status"] == status
                ),
            }
            for status in ("current", "early", "late", "critical")
        ],
    }
