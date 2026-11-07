from uuid import uuid4

from psycopg.types.json import Jsonb

from app.db.connection import connect
from app.integrations.channels.contracts import AuthorizedIntent
from app.integrations.channels.gateway import ChannelGateway
from app.integrations.knowledge.gateway import get_gateway
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.compliance.service import (
    binding_for,
    evaluate_contact_policy,
    issue_approval,
    sign_binding,
)
from app.modules.conversations.schemas import Message
from app.modules.knowledge_context.service import validate_citations
from app.modules.reviews.service import open_review
from app.modules.runs.repository import check_assignment, require_run
from app.shared.errors import DomainError
from app.shared.idempotency import record, replay


def message_json(row):
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
        message = {
            "id": str(uuid4()),
            "account_id": account_id,
            "run_id": None,
            "channel": "simulated_chat",
            "direction": "inbound",
            "text": request.text,
            "status": "received",
            "version": 1,
            "citation_ids": [],
            "created_at": now.isoformat(),
        }
        connection.execute(
            "INSERT INTO collection_messages VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                message["id"],
                account_id,
                None,
                "simulated_chat",
                "inbound",
                request.text,
                "received",
                1,
                Jsonb([]),
                now,
            ),
        )
        if request.demo_scenario in ("hardship", "dispute", "human_request"):
            open_review(connection, account_id, request.demo_scenario, now)
        append_event(connection, account_id, "message_received", caller.actor_kind, "received", now)
        record(connection, scope, str(request.idempotency_key), payload, message)
        return message


def submit_draft(settings, candidate, caller, now):
    with connect(settings.database_url) as connection:
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
        job = connection.execute(
            "SELECT * FROM collection_jobs WHERE run_id=%s", (run["id"],)
        ).fetchone()
        if job["execution_attempt_id"] != candidate.execution_attempt_id:
            raise DomainError("assignment_denied", "Execution attempt does not match", 403)
        snapshot, _refs = validate_citations(connection, run["id"], candidate.citation_ids)
        account = connection.execute(
            "SELECT * FROM accounts WHERE id=%s", (candidate.account_id,)
        ).fetchone()
        text = candidate.text_with_placeholders
        facts = {
            "borrower_name": account["borrower_name"],
            "outstanding_amount": f"Rp{account['outstanding_amount']:,}".replace(",", "."),
            "loan_reference": account["loan_reference"],
            "due_date": str(account["due_date"]),
        }
        for name, value in facts.items():
            text = text.replace("{" + name + "}", str(value))
        if "{" in text or "}" in text:
            raise DomainError("result_invalid", "Unknown fact placeholder")
        decision = evaluate_contact_policy(connection, candidate.account_id, now)
        forbidden = any(term in text.casefold() for term in ("ancam", "penjara", "permalukan"))
        mode = run["requested_runtime_mode"]
        approved = decision["allowed"] and mode == "simulated" and not forbidden
        message = {
            "id": str(uuid4()),
            "account_id": candidate.account_id,
            "run_id": run["id"],
            "channel": "simulated_chat",
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
                "simulated_chat",
                "outbound",
                text,
                message["status"],
                1,
                Jsonb(candidate.citation_ids),
                now,
            ),
        )
        if approved:
            issue_approval(connection, message, decision, snapshot, settings, now, mode)
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
            or ["guardrail_rejected" if forbidden else "guardrail_unavailable"],
        )
        record(connection, scope, str(candidate.idempotency_key), payload, message)
        return message


def simulate_dispatch(settings, message_id, request, caller, now):
    # The only available channel is a pure local simulator: there is no network in this transaction.
    with connect(settings.database_url) as connection:
        message = connection.execute(
            "SELECT * FROM collection_messages WHERE id=%s", (message_id,)
        ).fetchone()
        if not message:
            raise DomainError("not_found", "Message not found", 404)
        case = lock_case(connection, message["account_id"])
        message = connection.execute(
            "SELECT * FROM collection_messages WHERE id=%s FOR UPDATE", (message_id,)
        ).fetchone()
        payload = request.model_dump(mode="json")
        scope = "dispatch:" + message_id
        if old := replay(connection, scope, str(request.idempotency_key), payload):
            return old
        if message["version"] != request.expected_message_version:
            raise DomainError("version_conflict", "Message version has changed")
        decision = evaluate_contact_policy(connection, message["account_id"], now)
        reasons = list(decision["reason_codes"])
        origin = require_run(connection, message["run_id"], True) if message["run_id"] else None
        if (
            not origin
            or origin["status"] != "completed"
            or origin["outcome"] != "draft_created"
            or origin["cancellation_requested_at"]
        ):
            reasons.append("run_not_accepted")
        approval = connection.execute(
            "SELECT * FROM message_approvals WHERE message_id=%s", (message_id,)
        ).fetchone()
        if not approval:
            reasons.append("approval_invalid")
        elif approval["expires_at"] <= now:
            reasons.append("approval_expired")
        elif message["run_id"]:
            snapshot, refs = validate_citations(
                connection, message["run_id"], message["citation_ids"]
            )
            binding = binding_for(message, case, decision, snapshot, approval["expires_at"])
            import hmac

            if binding != approval["binding"] or not hmac.compare_digest(
                approval["attestation"],
                sign_binding(binding, settings.approval_hmac_secret.get_secret_value()),
            ):
                reasons.append("approval_invalid")
            try:
                valid = get_gateway(settings).validate_references(refs, now, caller)
                if not valid.valid:
                    reasons.append("knowledge_reference_invalid")
            except Exception:  # noqa: BLE001 - dependency failures must fail closed
                reasons.append("knowledge_unavailable")
        if connection.execute(
            "SELECT id FROM dispatch_receipts WHERE message_id=%s", (message_id,)
        ).fetchone():
            reasons.append("already_dispatched")
        if message["direction"] != "outbound" or message["status"] != "draft":
            reasons.append("message_not_dispatchable")
        status = "blocked"
        if not reasons:
            adapter = ChannelGateway().resolve(message["channel"])
            receipt = adapter.dispatch(
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
                "UPDATE collection_messages SET status='simulated_sent',version=version+1 "
                "WHERE id=%s",
                (message_id,),
            )
            status = "simulated_sent"
        event = append_event(
            connection,
            message["account_id"],
            "simulate_dispatch",
            caller.actor_kind,
            status,
            now,
            message["run_id"],
            reasons,
        )
        result = {
            "message_id": message_id,
            "status": status,
            "reason_codes": reasons,
            "audit_event_id": event["id"],
        }
        record(connection, scope, str(request.idempotency_key), payload, result)
        return result
