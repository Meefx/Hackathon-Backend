"""Authenticate WAHA events and apply only mapped self-test conversations."""

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

from app.config import Settings
from app.db.connection import connect
from app.integrations.waha.client import WahaClient
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.conversations.inbound import store_inbound
from app.modules.whatsapp import repository
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.shared.idempotency import digest

ACK_STATUS = {-1: "failed", 0: "submitted", 1: "sent", 2: "delivered", 3: "read", 4: "read"}
STATUS_RANK = {"delivery_unknown": -1, "submitted": 0, "sent": 1, "delivered": 2, "read": 3}


def receive(
    settings: Settings, body: bytes, signature: str, algorithm: str, now: datetime
) -> dict[str, str]:
    secret = settings.waha_webhook_secret.get_secret_value()
    if not settings.waha_collection_enabled or len(secret) < 32:
        raise DomainError("waha_webhook_not_configured", "WAHA webhook is unavailable", 503)
    expected = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    if algorithm != "sha512" or not hmac.compare_digest(expected.encode(), signature.encode()):
        raise DomainError("webhook_signature_invalid", "Webhook authentication failed", 401)
    try:
        event = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise DomainError("webhook_invalid", "Invalid webhook body", 422) from None
    if not isinstance(event, dict) or event.get("session") != settings.waha_session:
        raise DomainError("webhook_invalid", "Unexpected webhook session", 422)
    kind, payload = event.get("event"), event.get("payload")
    if not isinstance(kind, str) or kind not in {"message.any", "message.ack"}:
        return {"status": "ignored"}
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("id"), str)
        or not 1 <= len(payload["id"]) <= 512
    ):
        raise DomainError("webhook_invalid", "Webhook message ID is required", 422)
    provider_id = payload["id"]
    if kind == "message.any" and payload.get("source") != "app":
        return {"status": "ignored"}
    chat_id = None
    if kind == "message.any":
        chat_id = WahaClient(settings).own_chat_for_message(payload)
        if not chat_id:
            return {"status": "ignored"}
    message: dict[str, Any] = {}
    binding: dict[str, Any] = {}
    with connect(settings.database_url) as connection:
        if kind == "message.ack":
            if type(payload.get("ack")) is not int or payload["ack"] not in ACK_STATUS:
                return {"status": "ignored"}
            delivery = repository.delivery_by_provider(
                connection, settings.waha_session, provider_id
            )
            if not delivery:
                # An ACK may arrive before the sending transaction commits its receipt.
                own_chat_id = WahaClient(settings).self_chat_id()
                binding = (
                    repository.connection_by_chat(connection, settings.waha_session, own_chat_id)
                    or {}
                )
                if not binding:
                    return {"status": "ignored"}
                lock_case(connection, binding["account_id"])
                delivery = repository.delivery_by_provider(
                    connection, settings.waha_session, provider_id
                )
            if not delivery:
                return {"status": "ignored"}
            message = repository.message_for(connection, delivery["message_id"]) or {}
            if not message:
                return {"status": "ignored"}
            account_id = message["account_id"]
            semantic = {"event": kind, "id": provider_id, "ack": payload["ack"]}
        else:
            if repository.delivery_by_provider(connection, settings.waha_session, provider_id):
                return {"status": "ignored"}
            binding = (
                repository.connection_by_chat(
                    connection,
                    settings.waha_session,
                    chat_id,
                )
                or {}
            )
            if not binding:
                return {"status": "ignored"}
            text = payload.get("body")
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                return {"status": "ignored"}
            account_id = binding["account_id"]
            semantic = {
                "event": kind,
                "id": provider_id,
                "text": text,
                "chat_id": chat_id,
            }
        case = lock_case(connection, account_id)
        event_key = digest(
            {
                "session": settings.waha_session,
                "event": kind,
                "id": provider_id,
                "ack": payload.get("ack") if kind == "message.ack" else None,
            }
        )
        previous = repository.event_for(connection, event_key)
        if previous:
            if previous["payload_hash"] != digest(semantic):
                raise DomainError("webhook_replay_conflict", "Webhook event content changed")
            return {"status": "duplicate"}
        repository.save_event(
            connection, event_key, digest(semantic), settings.waha_session, provider_id, now
        )
        if kind == "message.ack":
            current = repository.lock_message_status(connection, message["id"])
            status = ACK_STATUS[payload["ack"]]
            if (
                status == "failed"
                and current not in {"delivered", "read"}
                or STATUS_RANK.get(status, -2) > STATUS_RANK.get(current, -1)
            ):
                repository.update_delivery_status(connection, message["id"], status)
                append_event(
                    connection,
                    account_id,
                    "whatsapp_delivery_status",
                    "provider",
                    status,
                    now,
                    correlation_id=message["id"],
                )
        else:
            if repository.delivery_by_provider(connection, settings.waha_session, provider_id):
                return {"status": "ignored"}
            if case["recipient_id"] != "whatsapp:" + binding["chat_id"]:
                return {"status": "ignored"}
            store_inbound(
                connection, account_id, text, Caller(actor_kind="provider"), now, "whatsapp"
            )
        return {"status": "processed"}
