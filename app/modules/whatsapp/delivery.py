"""Commit an intent before provider I/O so ambiguous writes are never replayed."""

from psycopg.types.json import Jsonb

from app.db.connection import connect
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.conversations.dispatch import dispatch_message, simulate_dispatch
from app.modules.whatsapp.repository import connection_for, delivery_for
from app.modules.whatsapp.service import require_operator, require_synthetic
from app.shared.errors import DomainError
from app.shared.idempotency import digest


def dispatch(settings, message_id, request, caller, clock):
    require_operator(caller)
    with connect(settings.database_url) as connection:
        row = connection.execute("SELECT channel FROM collection_messages WHERE id=%s", (message_id,)).fetchone()
        if not row:
            raise DomainError("not_found", "Message not found", 404)
    if row["channel"] != "whatsapp":
        return simulate_dispatch(settings, message_id, request, caller, clock)
    key = str(request.idempotency_key)
    payload_hash = digest(request.model_dump(mode="json"))
    with connect(settings.database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(71107002)")
        message = connection.execute("SELECT * FROM collection_messages WHERE id=%s", (message_id,)).fetchone()
        require_operator(caller, message["account_id"])
        lock_case(connection, message["account_id"])
        if previous := delivery_for(connection, message_id):
            if previous["intent_key"] != key or previous["payload_hash"] != payload_hash:
                raise DomainError("already_dispatched", "This message already has a delivery intent")
            return previous["response"] or {
                "message_id": message_id, "status": previous["status"],
                "reason_codes": ["delivery_confirmation_pending"], "audit_event_id": previous["audit_event_id"],
            }
        require_synthetic(connection, settings, message["account_id"])
        binding = connection_for(connection, message["account_id"])
        if not binding:
            raise DomainError("recipient_unverified", "Link the WhatsApp test recipient first", 403)
        if message["status"] != "draft" or message["version"] != request.expected_message_version:
            raise DomainError("version_conflict", "Message is no longer dispatchable")
        event = append_event(connection, message["account_id"], "whatsapp_dispatch_reserved", caller.actor_kind, "delivery_unknown", clock(), correlation_id=message_id)
        connection.execute(
            "INSERT INTO collection_whatsapp_deliveries(message_id,intent_key,payload_hash,session,chat_id,status,audit_event_id,created_at) VALUES (%s,%s,%s,%s,%s,'delivery_unknown',%s,%s)",
            (message_id, key, payload_hash, binding["session"], binding["chat_id"], event["id"], clock()),
        )
    try:
        with connect(settings.database_url) as connection:
            result = dispatch_message(connection, settings, message_id, request, caller, clock, whatsapp_intent_key=key)
            if result["status"] == "blocked":
                connection.execute("DELETE FROM collection_whatsapp_deliveries WHERE message_id=%s", (message_id,))
            else:
                connection.execute("UPDATE collection_whatsapp_deliveries SET response=%s WHERE message_id=%s", (Jsonb(result), message_id))
            return result
    except DomainError as error:
        status = "delivery_unknown" if error.code == "waha_delivery_unknown" else "failed"
        with connect(settings.database_url) as connection:
            lock_case(connection, message["account_id"])
            event = append_event(connection, message["account_id"], "whatsapp_dispatch", caller.actor_kind, status, clock(), reason_codes=[error.code], correlation_id=message_id)
            result = {"message_id": message_id, "status": status, "reason_codes": [error.code], "audit_event_id": event["id"]}
            connection.execute("UPDATE collection_messages SET status=%s,version=version+1 WHERE id=%s", (status, message_id))
            connection.execute("UPDATE collection_whatsapp_deliveries SET status=%s,response=%s WHERE message_id=%s", (status, Jsonb(result), message_id))
            return result
