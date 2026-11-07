"""Commit an intent before provider I/O so ambiguous writes are never replayed."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.config import Settings
from app.db.connection import connect
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.conversations.dispatch import dispatch_message, simulate_dispatch
from app.modules.conversations.schemas import DispatchRequest
from app.modules.whatsapp import repository
from app.modules.whatsapp.repository import connection_for, delivery_for
from app.modules.whatsapp.service import require_operator, require_synthetic
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.shared.idempotency import digest


def dispatch(
    settings: Settings,
    message_id: str,
    request: DispatchRequest,
    caller: Caller,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    require_operator(caller)
    with connect(settings.database_url) as connection:
        row = repository.message_for(connection, message_id)
        if not row:
            raise DomainError("not_found", "Message not found", 404)
    if row["channel"] != "whatsapp":
        return simulate_dispatch(settings, message_id, request, caller, clock)
    key = str(request.idempotency_key)
    payload_hash = digest(request.model_dump(mode="json"))
    with connect(settings.database_url) as connection:
        repository.lock_transport(connection)
        message = repository.message_for(connection, message_id)
        if not message:
            raise DomainError("not_found", "Message not found", 404)
        require_operator(caller, message["account_id"])
        lock_case(connection, message["account_id"])
        if previous := delivery_for(connection, message_id):
            if previous["intent_key"] != key or previous["payload_hash"] != payload_hash:
                raise DomainError(
                    "already_dispatched", "This message already has a delivery intent"
                )
            return previous["response"] or {
                "message_id": message_id,
                "status": previous["status"],
                "reason_codes": ["delivery_confirmation_pending"],
                "audit_event_id": previous["audit_event_id"],
            }
        require_synthetic(connection, settings, message["account_id"])
        binding = connection_for(connection, message["account_id"])
        if not binding:
            raise DomainError("recipient_unverified", "Link the WhatsApp test recipient first", 403)
        if message["status"] != "draft" or message["version"] != request.expected_message_version:
            raise DomainError("version_conflict", "Message is no longer dispatchable")
        event = append_event(
            connection,
            message["account_id"],
            "whatsapp_dispatch_reserved",
            caller.actor_kind,
            "delivery_unknown",
            clock(),
            correlation_id=message_id,
        )
        repository.reserve_delivery(
            connection, message_id, key, payload_hash, binding, event["id"], clock()
        )
    try:
        with connect(settings.database_url) as connection:
            result = dispatch_message(
                connection, settings, message_id, request, caller, clock, whatsapp_intent_key=key
            )
            if result["status"] == "blocked":
                repository.discard_delivery(connection, message_id)
            else:
                repository.save_response(connection, message_id, result)
            return result
    except DomainError as error:
        status = "delivery_unknown" if error.code == "waha_delivery_unknown" else "failed"
        with connect(settings.database_url) as connection:
            lock_case(connection, message["account_id"])
            event = append_event(
                connection,
                message["account_id"],
                "whatsapp_dispatch",
                caller.actor_kind,
                status,
                clock(),
                reason_codes=[error.code],
                correlation_id=message_id,
            )
            result = {
                "message_id": message_id,
                "status": status,
                "reason_codes": [error.code],
                "audit_event_id": event["id"],
            }
            repository.record_failure(connection, message_id, status, result)
            return result
