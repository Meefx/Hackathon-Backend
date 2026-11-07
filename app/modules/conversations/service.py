from dataclasses import replace
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.db.connection import connect
from app.integrations.knowledge.gateway import get_gateway
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.compliance.rendering import render_message
from app.modules.compliance.screening import (
    attest_candidate,
    screen_candidate,
)
from app.modules.compliance.service import evaluate_contact_policy
from app.modules.conversations.dispatch import simulate_dispatch  # noqa: F401
from app.modules.conversations.inbound import store_inbound
from app.modules.conversations.repository import save_binding, template_binding
from app.modules.conversations.schemas import Message
from app.modules.email_demo import service as email_demos
from app.modules.knowledge_context.service import validate_citations
from app.modules.runs.repository import check_assignment, require_run
from app.modules.whatsapp.service import draft_channel
from app.shared.errors import DomainError
from app.shared.idempotency import record, replay


def message_json(row):
    if row["status"] == "draft" and row.get("whatsapp_delivery_status"):
        row = {**row, "status": row["whatsapp_delivery_status"]}
    return Message.model_validate(row).model_dump(mode="json")


def receive_message(settings, account_id, request, caller, now):
    if not settings.demo_fixture_enabled:
        raise DomainError("synthetic_inbound_disabled", "Synthetic inbound is disabled", 403)
    with connect(settings.database_url) as connection:
        lock_case(connection, account_id)
        payload = request.model_dump(mode="json")
        scope = "receive:" + account_id
        if old := replay(connection, scope, str(request.idempotency_key), payload):
            return old
        message = store_inbound(
            connection,
            account_id,
            request.text,
            caller,
            now,
            "simulated_chat",
            request.demo_scenario,
        )
        record(connection, scope, str(request.idempotency_key), payload, message)
        return message


def submit_draft(settings, candidate, caller, now):
    with connect(settings.database_url) as connection:
        # Producer locks precede all operational/account locks.
        connection.execute("SELECT pg_advisory_xact_lock(71107002)")
        gateway = get_gateway(settings)
        template = None
        if candidate.template_id:
            if not hasattr(gateway, "load_template"):
                raise DomainError("knowledge_atomic_validation_unavailable")
            template = gateway.load_template(
                connection,
                candidate.template_id,
                replace(caller, account_id=candidate.account_id),
                now,
            )
        lock_case(connection, candidate.account_id)
        run = require_run(connection, candidate.run_id, True)
        scope = "draft:" + candidate.run_id
        payload = candidate.model_dump(mode="json")
        if old := replay(connection, scope, str(candidate.idempotency_key), payload):
            check_assignment(
                connection, caller, candidate.account_id, candidate.run_id, now, allow_inactive=True
            )
            return old
        check_assignment(connection, caller, candidate.account_id, candidate.run_id, now)
        if run["account_id"] != candidate.account_id or run["status"] != "running":
            raise DomainError("assignment_denied", "Draft requires active assigned run", 403)
        if run["payload"]["task_type"] != "follow_up":
            raise DomainError("task_forbidden", "This task only supports advisory artifacts", 403)
        job = connection.execute(
            "SELECT * FROM collection_jobs WHERE run_id=%s", (run["id"],)
        ).fetchone()
        if job["execution_attempt_id"] != candidate.execution_attempt_id:
            raise DomainError("assignment_denied", "Execution attempt does not match", 403)
        snapshot, _refs = validate_citations(connection, run["id"], candidate.citation_ids)
        if template and not set(template["document_ids"]).issubset(
            {ref.document_id for ref in _refs}
        ):
            raise DomainError("knowledge_reference_invalid", "Template evidence is incomplete")
        account = connection.execute(
            "SELECT * FROM accounts WHERE id=%s", (candidate.account_id,)
        ).fetchone()
        body = template["body"] if template else candidate.text_with_placeholders
        # A template candidate must match the exact approved artifact. No caller
        # supplied body can be smuggled through an unrelated template ID.
        if template and candidate.text_with_placeholders != body:
            raise DomainError("template_content_mismatch", "Candidate differs from template")
        text = render_message(body, account)
        email_demo = email_demos.is_demo_run(connection, run)
        channel = "email" if email_demo else draft_channel(connection, settings, run)
        if channel in {"whatsapp", "email"}:
            text = "[DEMO SINTETIS] " + text
        decision = evaluate_contact_policy(connection, candidate.account_id, now)
        mode = run["requested_runtime_mode"]
        screening = screen_candidate(text, mode=mode, fixture_enabled=settings.demo_fixture_enabled)
        approved = decision["allowed"] and screening["result"] == "ALLOW" and mode == "simulated"
        message = {
            "id": str(uuid4()),
            "account_id": candidate.account_id,
            "run_id": run["id"],
            "channel": channel,
            "direction": "outbound",
            "text": text,
            "status": "draft" if approved else "blocked",
            "version": 1,
            "citation_ids": candidate.citation_ids,
            "created_at": now.isoformat(),
        }
        connection.execute(
            "INSERT INTO collection_messages VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                message["id"],
                candidate.account_id,
                run["id"],
                channel,
                "outbound",
                text,
                message["status"],
                1,
                Jsonb(candidate.citation_ids),
                now,
            ),
        )
        if email_demo:
            email_demos.register_draft(connection, run["id"], message["id"], now)
        template = template_binding(template)
        save_binding(connection, message["id"], snapshot, template)
        snapshot = {**snapshot, "template_binding": template}
        if approved:
            attest_candidate(connection, message, decision, snapshot, settings, now, mode)
        connection.execute(
            "INSERT INTO run_artifacts VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid4()),
                run["id"],
                "draft",
                text,
                message["id"],
                Jsonb(candidate.citation_ids),
                "approved" if approved else "pending",
                now,
            ),
        )
        append_event(
            connection,
            candidate.account_id,
            "draft_created",
            caller.actor_kind,
            message["status"],
            now,
            run["id"],
            []
            if approved
            else decision["reason_codes"]
            or [code.lower() for code in screening["reason_codes"]]
            or ["guardrail_rejected"],
        )
        record(connection, scope, str(candidate.idempotency_key), payload, message)
        return message
