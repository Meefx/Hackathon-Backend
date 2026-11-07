"""Copy a legacy SQLite portfolio into migrated PostgreSQL, preserving the source."""

import argparse
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from psycopg import Error as DatabaseError
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db.connection import connect


class LegacyAccount(BaseModel):
    id: str = Field(strict=True)
    borrower_name: str = Field(strict=True)
    loan_reference: str = Field(strict=True)
    outstanding_amount: int = Field(strict=True, ge=0, le=2**63 - 1)
    due_date: date
    assigned_to: str = Field(strict=True)


def import_sqlite(source: Path, database_url: str) -> int:
    source = source.resolve(strict=True)
    # Explicit read-only URI prevents accidental source creation or modification.
    with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as legacy:
        legacy.row_factory = sqlite3.Row
        legacy.execute("BEGIN")
        accounts = [
            LegacyAccount.model_validate(dict(row)).model_dump()
            for row in legacy.execute(
                "SELECT id, borrower_name, loan_reference, outstanding_amount, due_date, "
                "assigned_to FROM accounts ORDER BY id"
            )
        ]
    inserted = 0
    with connect(database_url) as connection:
        for account in accounts:
            cursor = connection.execute(
                "INSERT INTO accounts "
                "(id, borrower_name, loan_reference, outstanding_amount, due_date, assigned_to) "
                "VALUES (%(id)s, %(borrower_name)s, %(loan_reference)s, "
                "%(outstanding_amount)s, %(due_date)s, %(assigned_to)s) ON CONFLICT DO NOTHING",
                account,
            )
            existing = connection.execute(
                "SELECT * FROM accounts WHERE id = %s", (account["id"],)
            ).fetchone()
            if existing != account:
                raise ValueError(f"Legacy account conflict for ID {account['id']!r}")
            inserted += cursor.rowcount
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Consistent legacy SQLite snapshot to read")
    args = parser.parse_args()
    try:
        count = import_sqlite(args.source, get_settings().database_url)
    except (OSError, sqlite3.Error, DatabaseError, ValueError):
        parser.exit(
            1,
            "Import failed. Check source data, conflicts, database connectivity and migrations "
            "before retrying.\n",
        )
    print(f"Imported {count} legacy accounts. Source preserved.")


if __name__ == "__main__":
    main()
