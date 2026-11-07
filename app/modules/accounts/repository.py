"""Account persistence. Schema changes belong to Alembic revisions."""

from app.db.connection import connect


def list_accounts(database_url: str) -> list[dict]:
    with connect(database_url) as connection:
        return connection.execute("SELECT * FROM accounts ORDER BY due_date, id").fetchall()


def get_account(database_url: str, account_id: str) -> dict | None:
    with connect(database_url) as connection:
        return connection.execute(
            "SELECT * FROM accounts WHERE id = %s", (account_id,)
        ).fetchone()
