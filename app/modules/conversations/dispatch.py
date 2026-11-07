"""The sole delivery boundary, sharing one transaction with receipt and audit."""

import hmac
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

from psycopg import Connection

from app.config import Settings
from app.core.clock import Clock
from app.db.connection import connect
from app.integrations.channels.contracts import AuthorizedIntent
from app.integrations.channels.gateway import ChannelGateway
from app.integrations.knowledge.gateway import get_gateway
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.compliance.repository import lock_contact_authority
from app.modules.compliance.sandbox import hermes_sandbox
from app.modules.compliance.screening import screen_candidate
from app.modules.compliance.service import binding_for, evaluate_contact_policy, sign_binding
from app.modules.conversations.compatibility import authorize_legacy_binding, bound_evidence
from app.modules.conversations.repository import message_binding, template_binding
from app.modules.conversations.schemas import DispatchRequest
from app.modules.knowledge_context.service import validate_citations
from app.modules.runs.repository import require_run
from app.modules.whatsapp.repository import connection_for, delivery_for, record_submission
from app.modules.whatsapp.service import require_synthetic
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.shared.idempotency import digest, record, replay
from app.trust.errors import TrustError
from app.trust.security import require


def current_time(clock: datetime | Callable[[], datetime] | Clock) -> datetime:
    if isinstance(clock, datetime):
        instant = clock
    elif callable(clock):
        instant = clock()
    else:
        instant = clock.now()
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise DomainError("clock_invalid", "Dispatch requires a timezone-aware clock", 503)
    return instant


def simulate_dispatch(
    settings: Settings,
    message_id: str,
    request: DispatchRequest,
    caller: Caller,
    clock: datetime | Callable[[], datetime] | Clock,
) -> dict[str, Any]:
    with connect(settings.database_url) as connection:
        return dispatch_message(connection, settings, message_id, request, caller, clock)


def dispatch_message(
    connection: Connection[dict[str, Any]],
    settings: Settings,
    message_id: str,
    request: DispatchRequest,
    caller: Caller,
    clock: datetime | Callable[[], datetime] | Clock,
    *,
    whatsapp_intent_key: str | None = None,
    email_demo_id: str | None = None,
) -> dict[str, Any]:
    # Scheduler provenance is persisted by server-owned admission, never caller input.
    operator = caller.actor_kind == "operator" and caller.principal_id == "local-operator"
    scheduler = (
        caller.actor_kind == "scheduler" and caller.principal_id == "daily-collection-scheduler"
    )
    assignment = None
    if scheduler:
        assignment = connection.execute(
            "SELECT s.id,s.facts_hash FROM collection_schedule_items s JOIN collection_runs r ON r.id=s.run_id "
            "JOIN collection_messages m ON m.run_id=r.id WHERE m.id=%s AND r.id=%s "
            "AND s.account_id=%s AND r.account_id=s.account_id AND m.account_id=s.account_id "
            "AND r.caller_id='daily-collection-scheduler' AND r.requested_runtime_mode='simulated' "
            "AND m.channel='simulated_chat'",
            (message_id, caller.run_id, caller.account_id),
        ).fetchone()
        scheduler = bool(assignment)
    if not operator and not scheduler:
        raise DomainError("dispatch_forbidden", "Trusted dispatch permission required", 403)
    connection.execute("SELECT pg_advisory_xact_lock(71107002)")
    initial = connection.execute(
        "SELECT * FROM collection_messages WHERE id=%s", (message_id,)
    ).fetchone()
    if not initial:
        raise DomainError("not_found", "Message not found", 404)
    if initial["channel"] == "whatsapp" and whatsapp_intent_key is None:
        raise DomainError("whatsapp_dispatch_required", "Use the WhatsApp dispatch endpoint", 422)
    if initial["channel"] == "email" and email_demo_id is None:
        raise DomainError("email_demo_dispatch_required", "Use the email demo workflow", 422)
    if caller.account_id and caller.account_id != initial["account_id"]:
        raise DomainError("assignment_denied", "Account assignment denied", 403)
    scoped = replace(caller, account_id=initial["account_id"])
    initial_binding = message_binding(connection, message_id)
    if not initial["run_id"]:
        authorize_legacy_binding(connection, initial_binding, initial["account_id"])
    refs, snapshot, guard, template = [], None, None, None
    reasons = []
    gateway = get_gateway(settings)
    try:
        if initial["run_id"]:
            snapshot, refs = validate_citations(
                connection, initial["run_id"], initial["citation_ids"]
            )
        else:
            # Runless messages can only be issued by the explicitly mapped
            # compatibility service, with a complete persisted evidence binding.
            snapshot, refs = bound_evidence(connection, initial_binding, initial["account_id"])
        if not hasattr(gateway, "lock_references"):
            reasons.append("knowledge_atomic_validation_unavailable")
        else:
            guard = gateway.lock_references(connection, refs, scoped)
        if initial_binding and initial_binding["template_binding"]:
            if not hasattr(gateway, "load_template"):
                reasons.append("knowledge_atomic_validation_unavailable")
            else:
                template = gateway.load_template(
                    connection,
                    initial_binding["template_binding"]["id"],
                    scoped,
                    current_time(clock),
                )
        template = template_binding(template)
    except Exception:  # noqa: BLE001 - unavailable authoritative evidence always denies
        reasons.append("knowledge_reference_invalid")
    case = lock_case(connection, initial["account_id"])
    lock_contact_authority(connection, initial["account_id"])
    message = connection.execute(
        "SELECT * FROM collection_messages WHERE id=%s FOR UPDATE", (message_id,)
    ).fetchone()
    if not message:
        raise DomainError("not_found", "Message not found", 404)
    persisted = message_binding(connection, message_id, lock=True)
    payload = request.model_dump(mode="json")
    scope = "dispatch:" + message_id
    previous = replay(connection, scope, str(request.idempotency_key), payload)
    channel_adapter = None
    if message["channel"] == "email":
        from app.modules.email_demo.delivery import authorize_delivery

        authorize_delivery(connection, settings, message, case, email_demo_id)
        if not previous:
            channel_adapter = ChannelGateway(settings).resolve("email")
            channel_adapter.prepare()
    if message["channel"] == "whatsapp" and not previous:
        channel_adapter = ChannelGateway(settings).resolve("whatsapp")
        # Complete provider identity I/O before evaluating the final time gates.
        channel_adapter.prepare()
    # Every potentially blocking authority/request lock and provider preflight has completed.
    now = current_time(clock)
    decision = evaluate_contact_policy(
        connection, message["account_id"], now, exclude_message_id=message_id
    )
    if message["channel"] == "whatsapp":
        require_synthetic(connection, settings, message["account_id"])
        whatsapp_binding = connection_for(connection, message["account_id"])
        intent = delivery_for(connection, message_id)
        if (
            not whatsapp_binding
            or not intent
            or intent["intent_key"] != whatsapp_intent_key
            or intent["chat_id"] != whatsapp_binding["chat_id"]
            or case["recipient_id"] != "whatsapp:" + whatsapp_binding["chat_id"]
        ):
            reasons.append("recipient_unverified")
    if not message["run_id"]:
        authorize_legacy_binding(connection, persisted, message["account_id"])
    if previous:
        # An old response confers no account access, but successful delivery stays
        # idempotent even when the policy or evidence has subsequently changed.
        return previous
    if message["version"] != request.expected_message_version:
        raise DomainError("version_conflict", "Message version has changed")
    if message != initial or persisted != initial_binding:
        reasons.append("message_binding_changed")
    try:
        if message["run_id"]:
            refreshed_snapshot, refreshed_refs = validate_citations(
                connection, message["run_id"], message["citation_ids"]
            )
        else:
            refreshed_snapshot, refreshed_refs = bound_evidence(
                connection, persisted, message["account_id"]
            )
        if refreshed_snapshot != snapshot or refreshed_refs != refs:
            reasons.append("message_binding_changed")
    except (DomainError, TrustError):
        reasons.append("knowledge_reference_invalid")
    reasons.extend(decision["reason_codes"])
    if scheduler:
        from zoneinfo import ZoneInfo

        portfolio = connection.execute(
            "SELECT outstanding_amount,due_date,borrower_name,loan_reference "
            "FROM accounts WHERE id=%s",
            (message["account_id"],),
        ).fetchone()
        if not portfolio or not assignment:
            raise DomainError("dispatch_forbidden", "Scheduled account binding unavailable", 403)
        if (
            portfolio["outstanding_amount"] <= 0
            or portfolio["due_date"] >= now.astimezone(ZoneInfo("Asia/Jakarta")).date()
        ):
            reasons.append("account_not_collectible")
        if assignment["facts_hash"] != digest(portfolio):
            reasons.append("account_facts_changed")
    if message["run_id"]:
        origin = require_run(connection, message["run_id"])
        if (
            origin["status"] != "completed"
            or origin["outcome"] != "draft_created"
            or origin["cancellation_requested_at"]
        ):
            reasons.append("run_not_accepted")
    if (
        not persisted
        or not snapshot
        or (
            persisted["snapshot_hash"] != snapshot["snapshot_hash"]
            or persisted["evidence_references"] != snapshot["references"]
        )
    ):
        reasons.append("approval_invalid")
    if guard:
        try:
            if not guard.validate(now).valid:
                reasons.append("knowledge_reference_invalid")
            if (
                persisted
                and persisted["template_binding"]
                and (
                    template != persisted["template_binding"]
                    or not guard.validate_template(persisted["template_binding"], now)
                )
            ):
                reasons.append("template_binding_invalid")
        except Exception:  # noqa: BLE001 - no alternate validation path on failure
            reasons.append("knowledge_unavailable")
    for ref in refs:
        if (
            ref.approved_at > now
            or (ref.effective_from and ref.effective_from > now)
            or (ref.effective_until and ref.effective_until <= now)
        ):
            reasons.append("knowledge_reference_invalid")
    approval = connection.execute(
        "SELECT * FROM message_approvals WHERE message_id=%s", (message_id,)
    ).fetchone()
    if not approval:
        reasons.append("approval_invalid")
    elif approval["expires_at"] <= now:
        reasons.append("approval_expired")
    elif snapshot:
        evidence = {
            **snapshot,
            "template_binding": persisted["template_binding"] if persisted else None,
        }
        try:
            issued = datetime.fromisoformat(approval["guardrail"]["evaluated_at"])
            if issued > now or not 0 < (approval["expires_at"] - issued).total_seconds() <= 120:
                reasons.append("approval_invalid")
        except (KeyError, ValueError, TypeError):
            reasons.append("approval_invalid")
        binding = binding_for(message, case, decision, evidence, approval["expires_at"])
        if binding != approval["binding"] or not hmac.compare_digest(
            approval["attestation"],
            sign_binding(binding, settings.approval_hmac_secret.get_secret_value()),
        ):
            reasons.append("approval_invalid")
        screening = screen_candidate(
            message["text"],
            mode=origin["requested_runtime_mode"] if message["run_id"] else "simulated",
            fixture_enabled=settings.demo_fixture_enabled,
            sandbox=hermes_sandbox(connection, settings, message),
        )
        if screening["result"] != "ALLOW":
            reasons.extend(code.lower() for code in screening["reason_codes"])
    if connection.execute(
        "SELECT id FROM dispatch_receipts WHERE message_id=%s", (message_id,)
    ).fetchone():
        reasons.append("already_dispatched")
    if message["direction"] != "outbound" or message["status"] != "draft":
        reasons.append("message_not_dispatchable")
    reasons = list(dict.fromkeys(reasons))
    status = "blocked"
    if not reasons:
        receipt = (
            channel_adapter or ChannelGateway(settings).resolve(message["channel"])
        ).dispatch(
            AuthorizedIntent(
                str(request.idempotency_key),
                message_id,
                message["account_id"],
                case["recipient_id"],
                message["text"],
            )
        )
        connection.execute(
            "INSERT INTO dispatch_receipts VALUES (%s,%s,%s,%s,%s,%s)",
            (
                receipt["id"],
                message_id,
                message["account_id"],
                str(request.idempotency_key),
                receipt["status"],
                now,
            ),
        )
        connection.execute(
            "UPDATE collection_messages SET status=%s,version=version+1 WHERE id=%s",
            (receipt["status"], message_id),
        )
        status = receipt["status"]
        if message["channel"] == "email":
            from app.modules.email_demo.service import record_submission as record_email_submission

            assert email_demo_id is not None
            record_email_submission(connection, email_demo_id, receipt["id"], now)
        if message["channel"] == "whatsapp":
            record_submission(connection, message_id, status, receipt["id"])
    event = append_event(
        connection,
        message["account_id"],
        {"whatsapp": "whatsapp_dispatch", "email": "email_demo_dispatch"}.get(
            message["channel"], "simulate_dispatch"
        ),
        caller.actor_kind,
        status,
        now,
        message["run_id"],
        reasons,
        correlation_id=message_id,
    )
    result = {
        "message_id": message_id,
        "status": status,
        "reason_codes": reasons,
        "audit_event_id": event["id"],
    }
    record(connection, scope, str(request.idempotency_key), payload, result)
    return result


def dispatch(conn, actor, prepared, secret, clock):
    """Legacy attempts are history. Explicit mapped runtime uses the collection gate."""
    require(actor, "attempt:create")
    from app.modules.conversations.compatibility import dispatch_legacy

    try:
        return dispatch_legacy(conn, actor, prepared, secret, clock)
    except DomainError as exc:
        raise TrustError(exc.code, exc.status) from exc
