"""Bind a fictional case to the authenticated WAHA account's own test chat."""

from app.db.connection import connect
from app.integrations.waha.client import WahaClient
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.compliance.repository import lock_contact_authority
from app.modules.whatsapp.repository import connection_for
from app.shared.errors import DomainError
from app.shared.idempotency import record, replay


def configured(settings) -> bool:
    return bool(
        settings.waha_collection_enabled
        and settings.demo_fixture_enabled
        and settings.waha_base_url
        and settings.waha_api_key.get_secret_value()
        and len(settings.waha_webhook_secret.get_secret_value()) >= 32
    )


def require_operator(caller, account_id=None):
    if caller.actor_kind != "operator" or caller.principal_id != "local-operator":
        raise DomainError("dispatch_forbidden", "Local operator permission is required", 403)
    if caller.account_id and caller.account_id != account_id:
        raise DomainError("assignment_denied", "Account assignment denied", 403)


def require_synthetic(connection, settings, account_id):
    if not configured(settings):
        raise DomainError("waha_not_configured", "Enable the local WhatsApp collection sandbox", 503)
    authority = lock_contact_authority(connection, account_id)
    if not authority or not authority["is_synthetic"]:
        raise DomainError("whatsapp_synthetic_only", "WhatsApp test bindings require a synthetic case", 403)


def status(settings, account_id, caller):
    require_operator(caller, account_id)
    with connect(settings.database_url) as connection:
        lock_case(connection, account_id)
        binding = connection_for(connection, account_id)
    result = {
        "account_id": account_id, "configured": configured(settings), "linked": bool(binding),
        "session_status": "unavailable", "recipient_masked": None, "reason_codes": [],
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


def link(settings, account_id, request, caller, now):
    require_operator(caller, account_id)
    if not configured(settings):
        raise DomainError("waha_not_configured", "Configure the WhatsApp sandbox first", 503)
    client = WahaClient(settings)
    chat_id = client.self_chat_id()
    client.configure_webhook()
    with connect(settings.database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(71107002)")
        case = lock_case(connection, account_id)
        require_synthetic(connection, settings, account_id)
        payload = request.model_dump(mode="json")
        scope = "whatsapp-link:" + account_id
        if old := replay(connection, scope, str(request.idempotency_key), payload):
            return old
        existing = connection.execute(
            "SELECT account_id FROM collection_whatsapp_connections WHERE session=%s AND chat_id=%s",
            (settings.waha_session, chat_id),
        ).fetchone()
        if existing and existing["account_id"] != account_id:
            raise DomainError("whatsapp_already_linked", "Disconnect the existing case before linking another")
        binding = connection_for(connection, account_id)
        if not binding:
            if not case["recipient_verified"] or case["recipient_id"] != "synthetic:" + account_id:
                raise DomainError("recipient_unverified", "An explicit synthetic recipient is required", 403)
            connection.execute(
                "INSERT INTO collection_whatsapp_connections VALUES (%s,%s,%s,%s,%s)",
                (account_id, settings.waha_session, chat_id, case["recipient_id"], now),
            )
            connection.execute(
                "UPDATE collection_cases SET recipient_id=%s,version=version+1,updated_at=%s WHERE account_id=%s",
                ("whatsapp:" + chat_id, now, account_id),
            )
        elif binding["chat_id"] != chat_id:
            raise DomainError("waha_identity_changed", "Disconnect the previous WhatsApp identity first")
        append_event(connection, account_id, "whatsapp_linked", caller.actor_kind, "sandbox", now)
        result = {
            "account_id": account_id, "configured": True, "linked": True,
            "session_status": "WORKING", "recipient_masked": "••••" + chat_id.split("@")[0][-4:],
            "reason_codes": [],
        }
        record(connection, scope, str(request.idempotency_key), payload, result)
        return result


def disconnect(settings, account_id, caller, now):
    require_operator(caller, account_id)
    with connect(settings.database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(71107002)")
        lock_case(connection, account_id)
        binding = connection_for(connection, account_id)
        if binding:
            connection.execute("DELETE FROM collection_whatsapp_connections WHERE account_id=%s", (account_id,))
            connection.execute(
                "UPDATE collection_cases SET recipient_id=%s,version=version+1,updated_at=%s WHERE account_id=%s",
                (binding["previous_recipient_id"], now, account_id),
            )
            append_event(connection, account_id, "whatsapp_disconnected", caller.actor_kind, "disconnected", now)
    return status(settings, account_id, caller)
