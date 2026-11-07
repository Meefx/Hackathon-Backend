"""Bind a fictional case to the authenticated WAHA account's own test chat."""

from datetime import datetime
from typing import Any

from psycopg import Connection

from app.config import Settings
from app.db.connection import connect
from app.integrations.waha.client import WahaClient
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.compliance.repository import lock_contact_authority
from app.modules.whatsapp import repository
from app.modules.whatsapp.repository import connection_for
from app.modules.whatsapp.schemas import LinkRequest
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.shared.idempotency import record, replay


def configured(settings: Settings) -> bool:
    return bool(
        settings.waha_collection_enabled
        and settings.demo_fixture_enabled
        and settings.waha_base_url
        and settings.waha_api_key.get_secret_value()
        and len(settings.waha_webhook_secret.get_secret_value()) >= 32
    )


def matches_test_recipient(
    connection: Connection[dict[str, Any]], settings: Settings, account_id: str, recipient_id: str
) -> bool:
    binding = connection_for(connection, account_id)
    return bool(
        configured(settings)
        and binding
        and binding["session"] == settings.waha_session
        and recipient_id == "whatsapp:" + binding["chat_id"]
    )


def require_operator(caller: Caller, account_id: str | None = None) -> None:
    if caller.actor_kind != "operator" or caller.principal_id != "local-operator":
        raise DomainError("dispatch_forbidden", "Local operator permission is required", 403)
    if caller.account_id and caller.account_id != account_id:
        raise DomainError("assignment_denied", "Account assignment denied", 403)


def require_synthetic(
    connection: Connection[dict[str, Any]], settings: Settings, account_id: str
) -> None:
    if not configured(settings):
        raise DomainError(
            "waha_not_configured", "Enable the local WhatsApp collection sandbox", 503
        )
    authority = lock_contact_authority(connection, account_id)
    if not authority or not authority["is_synthetic"]:
        raise DomainError(
            "whatsapp_synthetic_only", "WhatsApp test bindings require a synthetic case", 403
        )


def draft_channel(
    connection: Connection[dict[str, Any]], settings: Settings, run: dict[str, Any]
) -> str:
    if (
        configured(settings)
        and run["caller_id"] == "local-operator"
        and connection_for(connection, run["account_id"])
    ):
        return "whatsapp"
    return "simulated_chat"


def status(settings: Settings, account_id: str, caller: Caller) -> dict[str, Any]:
    require_operator(caller, account_id)
    with connect(settings.database_url) as connection:
        lock_case(connection, account_id)
        binding = connection_for(connection, account_id)
    result: dict[str, Any] = {
        "account_id": account_id,
        "configured": configured(settings),
        "linked": bool(binding),
        "session_status": "unavailable",
        "recipient_masked": None,
        "reason_codes": [],
    }
    if binding:
        result["recipient_masked"] = "••••" + binding["chat_id"].split("@")[0][-4:]
    if result["configured"]:
        try:
            result["session_status"] = WahaClient(settings).session_status()["status"]
        except DomainError as error:
            result["reason_codes"] = [error.code]
    else:
        result["reason_codes"] = ["waha_not_configured"]
    return result


def link(
    settings: Settings, account_id: str, request: LinkRequest, caller: Caller, now: datetime
) -> dict[str, Any]:
    require_operator(caller, account_id)
    if not configured(settings):
        raise DomainError("waha_not_configured", "Configure the WhatsApp sandbox first", 503)
    with connect(settings.database_url) as connection:
        lock_case(connection, account_id)
        require_synthetic(connection, settings, account_id)
    client = WahaClient(settings)
    chat_id = client.self_chat_id()
    client.configure_webhook()
    with connect(settings.database_url) as connection:
        repository.lock_transport(connection)
        case = lock_case(connection, account_id)
        require_synthetic(connection, settings, account_id)
        payload = request.model_dump(mode="json")
        scope = "whatsapp-link:" + account_id
        if old := replay(connection, scope, str(request.idempotency_key), payload):
            return old
        existing = repository.connection_by_chat(connection, settings.waha_session, chat_id)
        if existing and existing["account_id"] != account_id:
            raise DomainError(
                "whatsapp_already_linked", "Disconnect the existing case before linking another"
            )
        binding = connection_for(connection, account_id)
        if not binding:
            if not case["recipient_verified"] or case["recipient_id"] != "synthetic:" + account_id:
                raise DomainError(
                    "recipient_unverified", "An explicit synthetic recipient is required", 403
                )
            repository.link_case(
                connection, account_id, settings.waha_session, chat_id, case["recipient_id"], now
            )
        elif binding["chat_id"] != chat_id:
            raise DomainError(
                "waha_identity_changed", "Disconnect the previous WhatsApp identity first"
            )
        append_event(connection, account_id, "whatsapp_linked", caller.actor_kind, "sandbox", now)
        result: dict[str, Any] = {
            "account_id": account_id,
            "configured": True,
            "linked": True,
            "session_status": "WORKING",
            "recipient_masked": "••••" + chat_id.split("@")[0][-4:],
            "reason_codes": [],
        }
        record(connection, scope, str(request.idempotency_key), payload, result)
        return result


def disconnect(
    settings: Settings, account_id: str, caller: Caller, now: datetime
) -> dict[str, Any]:
    require_operator(caller, account_id)
    with connect(settings.database_url) as connection:
        repository.lock_transport(connection)
        lock_case(connection, account_id)
        binding = connection_for(connection, account_id)
        if binding:
            repository.unlink_case(connection, account_id, binding["previous_recipient_id"], now)
            append_event(
                connection,
                account_id,
                "whatsapp_disconnected",
                caller.actor_kind,
                "disconnected",
                now,
            )
    return status(settings, account_id, caller)
