"""Gmail demonstration admission and delivery persistence."""

from datetime import datetime
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.shared.errors import DomainError

SELECT_DEMO = (
    "SELECT d.*,r.status AS run_status,r.reason_codes AS run_reasons,r.outcome "
    "FROM collection_email_demos d JOIN collection_runs r ON r.id=d.run_id "
)


def get_demo(
    connection: Connection[dict[str, Any]], demo_id: str, caller_id: str
) -> dict[str, Any]:
    row = connection.execute(
        SELECT_DEMO + "WHERE d.id=%s AND d.caller_id=%s", (demo_id, caller_id)
    ).fetchone()
    if not row:
        raise DomainError("not_found", "Email collection demo not found", 404)
    return row


def latest(connection: Connection[dict[str, Any]], caller_id: str) -> dict[str, Any] | None:
    return connection.execute(
        SELECT_DEMO + "WHERE d.caller_id=%s ORDER BY d.created_at DESC,d.id DESC LIMIT 1",
        (caller_id,),
    ).fetchone()


def for_run(connection: Connection[dict[str, Any]], run_id: str) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT * FROM collection_email_demos WHERE run_id=%s", (run_id,)
    ).fetchone()


def for_message(connection: Connection[dict[str, Any]], message_id: str) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT * FROM collection_email_demos WHERE message_id=%s", (message_id,)
    ).fetchone()


def attach_message(
    connection: Connection[dict[str, Any]], run_id: str, message_id: str, now: datetime
) -> None:
    connection.execute(
        "UPDATE collection_email_demos SET message_id=%s,updated_at=%s WHERE run_id=%s",
        (message_id, now, run_id),
    )


def update(
    connection: Connection[dict[str, Any]],
    demo_id: str,
    status: str,
    reasons: list[str],
    now: datetime,
    provider_id: str | None = None,
) -> None:
    connection.execute(
        "UPDATE collection_email_demos SET status=%s,reason_codes=%s,updated_at=%s,"
        "provider_message_id=COALESCE(%s,provider_message_id) WHERE id=%s",
        (status, Jsonb(reasons), now, provider_id, demo_id),
    )


def pending(connection: Connection[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    return connection.execute(
        "SELECT id,caller_id FROM collection_email_demos WHERE status IN ('queued','running') "
        "AND (reserved_at IS NULL OR delivery_deadline_at<=%s) "
        "ORDER BY created_at,id LIMIT 20",
        (now,),
    ).fetchall()
