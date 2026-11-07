"""Bounded case evidence for an operator's manager discussion."""

from typing import Any

from psycopg import Connection


def recent_messages(connection: Connection[dict[str, Any]], account_id: str) -> list[dict[str, Any]]:
    return connection.execute(
        "SELECT direction,text,created_at FROM collection_messages WHERE account_id=%s "
        "ORDER BY created_at DESC,id DESC LIMIT 5",
        (account_id,),
    ).fetchall()[::-1]


def originating_run(
    connection: Connection[dict[str, Any]], run_id: str | None
) -> dict[str, Any] | None:
    if not run_id:
        return None
    return connection.execute(
        "SELECT id,status,outcome,reason_codes FROM collection_runs WHERE id=%s", (run_id,)
    ).fetchone()
