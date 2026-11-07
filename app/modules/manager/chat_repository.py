"""Persistence for portfolio chat sessions, context, and advisory artifacts."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.shared.errors import DomainError


def require_context_account(connection: Connection[dict[str, Any]], account_id: str | None) -> None:
    if (
        account_id is not None
        and not connection.execute("SELECT id FROM accounts WHERE id=%s", (account_id,)).fetchone()
    ):
        raise DomainError("not_found", "Context account not found", 404)


def require_session(
    connection: Connection[dict[str, Any]], session_id: str, account_id: str | None, caller_id: str
) -> dict[str, Any]:
    session = connection.execute(
        "SELECT * FROM manager_sessions WHERE id=%s FOR UPDATE", (session_id,)
    ).fetchone()
    if not session or session["account_id"] != account_id or session["caller_id"] != caller_id:
        raise DomainError("session_denied", "Session does not belong to this account/caller", 403)
    return session


def create_session(
    connection: Connection[dict[str, Any]],
    session_id: str,
    account_id: str | None,
    caller_id: str,
    title: str,
    now: datetime,
) -> None:
    connection.execute(
        "INSERT INTO manager_sessions(id,account_id,caller_id,title,created_at,updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (session_id, account_id, caller_id, title, now, now),
    )


def next_turn(connection: Connection[dict[str, Any]], session_id: str) -> int:
    row = connection.execute(
        "UPDATE manager_sessions SET next_turn=next_turn+1 "
        "WHERE id=%s RETURNING next_turn-1 AS turn",
        (session_id,),
    ).fetchone()
    assert row is not None
    return row["turn"]


def update_context(
    connection: Connection[dict[str, Any]], session_id: str, account_id: str | None
) -> None:
    connection.execute(
        "UPDATE manager_sessions SET context_account_id=%s WHERE id=%s", (account_id, session_id)
    )


def list_sessions(
    connection: Connection[dict[str, Any]], caller_id: str, limit: int, offset: int
) -> dict:
    items = connection.execute(
        "SELECT * FROM manager_sessions WHERE account_id IS NULL AND caller_id=%s "
        "ORDER BY updated_at DESC,id DESC LIMIT %s OFFSET %s",
        (caller_id, limit, offset),
    ).fetchall()
    total = connection.execute(
        "SELECT count(*) AS n FROM manager_sessions WHERE account_id IS NULL AND caller_id=%s",
        (caller_id,),
    ).fetchone()
    assert total is not None
    return {"items": items, "total": total["n"], "limit": limit, "offset": offset}


def get_session(
    connection: Connection[dict[str, Any]], session_id: str, caller_id: str
) -> dict[str, Any]:
    session = connection.execute(
        "SELECT * FROM manager_sessions WHERE id=%s AND caller_id=%s AND account_id IS NULL",
        (session_id, caller_id),
    ).fetchone()
    if not session:
        raise DomainError("not_found", "Manager chat session not found", 404)
    return session


def recover_request(connection: Connection[dict[str, Any]], caller_id: str, key: str) -> dict:
    row = connection.execute(
        "SELECT response FROM collection_requests WHERE scope=%s AND key=%s",
        ("start_manager_chat:" + caller_id, key),
    ).fetchone()
    if not row:
        raise DomainError("not_found", "Manager chat request not found", 404)
    return row["response"]


def recent_messages(connection: Connection[dict[str, Any]], account_id: str) -> list[dict]:
    return connection.execute(
        "SELECT direction,text,created_at FROM collection_messages WHERE account_id=%s "
        "ORDER BY created_at DESC,id DESC LIMIT 5",
        (account_id,),
    ).fetchall()[::-1]


def open_reviews(connection: Connection[dict[str, Any]], account_id: str) -> list[dict]:
    return connection.execute(
        "SELECT id,account_id,run_id,reason,status,version,created_at,resolved_at "
        "FROM collection_reviews WHERE account_id=%s AND status='open' "
        "ORDER BY created_at DESC,id DESC LIMIT 20",
        (account_id,),
    ).fetchall()


def add_artifact(
    connection: Connection[dict[str, Any]],
    run_id: str,
    kind: str,
    text: str,
    now: datetime,
    proposal: dict | None = None,
) -> dict:
    artifact = connection.execute(
        "INSERT INTO run_artifacts(id,run_id,kind,text,created_at,proposal) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
        (str(uuid4()), run_id, kind, text, now, Jsonb(proposal) if proposal else None),
    ).fetchone()
    assert artifact is not None
    return artifact
