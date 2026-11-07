"""WhatsApp account bindings and durable transport records."""

from datetime import datetime
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb


def outbound_messages(
    connection: Connection[dict[str, Any]], limit: int, offset: int
) -> dict[str, Any]:
    # Count and page share one snapshot, including when the requested page is empty.
    row = connection.execute(
        "WITH outbound AS ("
        "SELECT m.*,a.borrower_name,a.loan_reference,"
        "CASE WHEN m.status='draft' THEN COALESCE(d.status,m.status) "
        "ELSE m.status END AS delivery_status "
        "FROM collection_messages m JOIN accounts a ON a.id=m.account_id "
        "LEFT JOIN collection_whatsapp_deliveries d ON d.message_id=m.id "
        "WHERE m.channel='whatsapp' AND m.direction='outbound'),"
        "visible AS (SELECT * FROM outbound WHERE delivery_status IN "
        "('submitted','sent','delivered','read','failed','delivery_unknown')) "
        "SELECT (SELECT count(*) FROM visible) AS total,"
        "(SELECT COALESCE(jsonb_agg(page),'[]'::jsonb) FROM "
        "(SELECT * FROM visible ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s) page) AS items",
        (limit, offset),
    ).fetchone()
    assert row is not None
    return row


def connection_for(
    connection: Connection[dict[str, Any]], account_id: str
) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT * FROM collection_whatsapp_connections WHERE account_id=%s", (account_id,)
    ).fetchone()


def delivery_for(connection: Connection[dict[str, Any]], message_id: str) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT * FROM collection_whatsapp_deliveries WHERE message_id=%s", (message_id,)
    ).fetchone()


def lock_transport(connection: Connection[dict[str, Any]]) -> None:
    connection.execute("SELECT pg_advisory_xact_lock(71107002)")


def message_for(connection: Connection[dict[str, Any]], message_id: str) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT * FROM collection_messages WHERE id=%s", (message_id,)
    ).fetchone()


def connection_by_chat(
    connection: Connection[dict[str, Any]], session: str, chat_id: str | None
) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT * FROM collection_whatsapp_connections WHERE session=%s AND chat_id=%s",
        (session, chat_id),
    ).fetchone()


def link_case(
    connection: Connection[dict[str, Any]],
    account_id: str,
    session: str,
    chat_id: str,
    previous_recipient: str,
    now: datetime,
) -> None:
    connection.execute(
        "INSERT INTO collection_whatsapp_connections VALUES (%s,%s,%s,%s,%s)",
        (account_id, session, chat_id, previous_recipient, now),
    )
    connection.execute(
        "UPDATE collection_cases SET recipient_id=%s,version=version+1,updated_at=%s WHERE account_id=%s",
        ("whatsapp:" + chat_id, now, account_id),
    )


def unlink_case(
    connection: Connection[dict[str, Any]], account_id: str, previous_recipient: str, now: datetime
) -> None:
    connection.execute(
        "DELETE FROM collection_whatsapp_connections WHERE account_id=%s", (account_id,)
    )
    connection.execute(
        "UPDATE collection_cases SET recipient_id=%s,version=version+1,updated_at=%s WHERE account_id=%s",
        (previous_recipient, now, account_id),
    )


def reserve_delivery(
    connection: Connection[dict[str, Any]],
    message_id: str,
    key: str,
    payload_hash: str,
    binding: dict[str, Any],
    audit_id: str,
    now: datetime,
) -> None:
    connection.execute(
        "INSERT INTO collection_whatsapp_deliveries(message_id,intent_key,payload_hash,session,chat_id,status,audit_event_id,created_at) "
        "VALUES (%s,%s,%s,%s,%s,'delivery_unknown',%s,%s)",
        (message_id, key, payload_hash, binding["session"], binding["chat_id"], audit_id, now),
    )


def discard_delivery(connection: Connection[dict[str, Any]], message_id: str) -> None:
    connection.execute(
        "DELETE FROM collection_whatsapp_deliveries WHERE message_id=%s", (message_id,)
    )


def save_response(
    connection: Connection[dict[str, Any]], message_id: str, result: dict[str, Any]
) -> None:
    connection.execute(
        "UPDATE collection_whatsapp_deliveries SET response=%s WHERE message_id=%s",
        (Jsonb(result), message_id),
    )


def record_submission(
    connection: Connection[dict[str, Any]], message_id: str, status: str, provider_id: str
) -> None:
    connection.execute(
        "UPDATE collection_whatsapp_deliveries SET status=%s,provider_message_id=%s WHERE message_id=%s",
        (status, provider_id, message_id),
    )


def record_failure(
    connection: Connection[dict[str, Any]], message_id: str, status: str, result: dict[str, Any]
) -> None:
    connection.execute(
        "UPDATE collection_messages SET status=%s,version=version+1 WHERE id=%s",
        (status, message_id),
    )
    connection.execute(
        "UPDATE collection_whatsapp_deliveries SET status=%s,response=%s WHERE message_id=%s",
        (status, Jsonb(result), message_id),
    )


def delivery_by_provider(
    connection: Connection[dict[str, Any]], session: str, provider_id: str
) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT * FROM collection_whatsapp_deliveries WHERE provider_message_id=%s AND session=%s",
        (provider_id, session),
    ).fetchone()


def event_for(connection: Connection[dict[str, Any]], event_key: str) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT payload_hash FROM collection_whatsapp_events WHERE event_key=%s", (event_key,)
    ).fetchone()


def save_event(
    connection: Connection[dict[str, Any]],
    event_key: str,
    payload_hash: str,
    session: str,
    provider_id: str,
    now: datetime,
) -> None:
    connection.execute(
        "INSERT INTO collection_whatsapp_events VALUES (%s,%s,%s,%s,%s)",
        (event_key, payload_hash, session, provider_id, now),
    )


def lock_message_status(connection: Connection[dict[str, Any]], message_id: str) -> str:
    row = connection.execute(
        "SELECT status FROM collection_messages WHERE id=%s FOR UPDATE", (message_id,)
    ).fetchone()
    assert row is not None
    return row["status"]


def update_delivery_status(
    connection: Connection[dict[str, Any]], message_id: str, status: str
) -> None:
    connection.execute(
        "UPDATE collection_messages SET status=%s,version=version+1 WHERE id=%s",
        (status, message_id),
    )
    connection.execute(
        "UPDATE collection_whatsapp_deliveries SET status=%s WHERE message_id=%s",
        (status, message_id),
    )
    connection.execute(
        "UPDATE dispatch_receipts SET status=%s WHERE message_id=%s", (status, message_id)
    )
