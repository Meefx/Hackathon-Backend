"""Explicit legacy-to-account bridge. New effects use collection storage only."""

import hashlib
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import SecretStr

from app.config import get_settings
from app.integrations.knowledge.trust_adapter import InProcessKnowledgeGateway
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.compliance.rendering import render_message
from app.modules.compliance.screening import attest_candidate, inbound_categories
from app.modules.compliance.service import evaluate_contact_policy, get_authority
from app.modules.conversations.repository import save_binding, template_binding
from app.modules.conversations.schemas import DispatchRequest
from app.modules.knowledge_context.contracts import KnowledgeResult, KnowledgeSource
from app.modules.knowledge_context.service import snapshot_hash, validate_snapshot_integrity
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.shared.idempotency import record, replay
from app.trust.errors import TrustError
from app.trust.safety import screen_message
from app.trust.security import Actor, require


def mapped_account(connection, actor, case_id):
    row = connection.execute(
        "SELECT c.*,m.account_id FROM trust.cases c "
        "JOIN legacy_case_account_mappings m ON m.case_id=c.id "
        "WHERE c.id=%s AND c.principal=%s AND c.product=%s",
        (UUID(str(case_id)), actor.principal, actor.product),
    ).fetchone()
    if not row:
        raise TrustError("legacy_case_mapping_required", 409)
    current = get_authority(connection, row["account_id"])
    if (
        current["principal"] != actor.principal
        or current["product"] != actor.product
        or str(current["borrower_ref"]) != str(row["borrower_ref"])
        or current["is_synthetic"] is not True
    ):
        raise TrustError("legacy_case_mapping_required", 409)
    return row


def collection_settings(connection, secret):
    # The compatibility transaction never opens another operational connection.
    return get_settings().model_copy(
        update={
            "database_url": connection.info.dsn,
            "knowledge_mode": "in_process",
            "knowledge_service_factory": "app.integrations.knowledge.trust_adapter:create_gateway",
            "approval_hmac_secret": SecretStr(secret),
        }
    )


def bound_evidence(connection, persisted, account_id):
    if not persisted or len(persisted["evidence_references"]) != 1:
        raise DomainError("approval_invalid")
    envelope = persisted["evidence_references"][0]
    current = get_authority(connection, account_id)
    actor = Actor("local-operator", "operator", current["principal"], current["product"])
    mapped = mapped_account(connection, actor, envelope["legacy_case_id"])
    if mapped["account_id"] != account_id:
        raise DomainError("legacy_case_mapping_required")
    result = KnowledgeResult.model_validate(envelope["result"])
    payload = result.model_dump(mode="json")
    validate_snapshot_integrity(
        {
            **payload,
            "snapshot_hash": persisted["snapshot_hash"],
            "snapshot_hash_version": envelope.get("snapshot_hash_version", 2),
        }
    )
    return {
        "snapshot_hash": persisted["snapshot_hash"],
        "references": persisted["evidence_references"],
    }, [KnowledgeSource.model_validate(source) for source in payload["sources"]]


def prepare_legacy(connection, actor, case_id, template_id, key, secret, clock, layer2):
    from app.modules.conversations.dispatch import current_time

    require(actor, "attempt:create")
    connection.execute("SELECT pg_advisory_xact_lock(71107002)")
    mapped = mapped_account(connection, actor, case_id)
    payload = {"case_id": str(case_id), "template_id": str(template_id)}
    scope = "legacy-prepare:" + mapped["account_id"]
    old_attempt = connection.execute(
        "SELECT id,status FROM trust.attempts WHERE principal=%s AND product=%s "
        "AND case_id=%s AND idempotency_key=%s",
        (actor.principal, actor.product, UUID(str(case_id)), UUID(str(key))),
    ).fetchone()
    if old_attempt:
        # Never manufacture a fresh approval/receipt for pre-cutover attempts.
        return {
            "attempt_id": str(old_attempt["id"]),
            "status": old_attempt["status"],
            "idempotent_replay": True,
            "is_simulated": True,
        }
    settings = collection_settings(connection, secret)
    caller = Caller(principal_id="local-operator", account_id=mapped["account_id"])
    gateway = InProcessKnowledgeGateway(settings=settings, clock=lambda: current_time(clock))
    try:
        template = gateway.load_template(connection, str(template_id), caller, current_time(clock))
        result = gateway.template_context(connection, template, caller, current_time(clock))
        if result.status != "ready":
            raise TrustError(result.reason_code or "knowledge_unavailable", 409)
        guard = gateway.lock_references(connection, result.sources, caller)
    except TrustError as exc:
        lock_case(connection, mapped["account_id"])
        previous = replay(connection, scope, str(key), payload)
        if previous:
            return {**previous, "idempotent_replay": True}
        blocked = {
            "attempt_id": str(key),
            "status": "NOT_SENT",
            "reason_codes": [exc.code],
            "is_simulated": True,
        }
        record(connection, scope, str(key), payload, blocked)
        return blocked
    lock_case(connection, mapped["account_id"])
    from app.modules.compliance.repository import lock_contact_authority

    lock_contact_authority(connection, mapped["account_id"])
    previous = replay(connection, scope, str(key), payload)
    now = current_time(clock)
    if previous:
        return {**previous, "idempotent_replay": True}
    if not guard.validate(now).valid or not guard.validate_template(template, now):
        raise TrustError("knowledge_reference_invalid", 409)
    account = connection.execute(
        "SELECT * FROM accounts WHERE id=%s", (mapped["account_id"],)
    ).fetchone()
    text = render_message(template["body"], account)
    delivered = connection.execute(
        "SELECT 1 FROM dispatch_receipts WHERE account_id=%s LIMIT 1", (mapped["account_id"],)
    ).fetchone()
    if not delivered and "automated assistant" not in text.casefold():
        text = "I am an automated assistant for Synthetic Lender. " + text
    policy = evaluate_contact_policy(connection, mapped["account_id"], now)
    try:
        label = layer2.evaluate(text) if settings.demo_fixture_enabled else None
    except Exception:  # noqa: BLE001 - unavailable classifier always blocks
        label = None
    screening = screen_message(text, label)
    approved = policy["allowed"] and screening["result"] == "ALLOW"
    evidence_payload = result.model_dump(mode="json")
    evidence = {
        "snapshot_hash": snapshot_hash(
            evidence_payload["sources"],
            evidence_payload["critical_context"],
            evidence_payload["verification_profile"],
        ),
        "references": [
            {"result": evidence_payload, "legacy_case_id": str(case_id), "snapshot_hash_version": 2}
        ],
    }
    message = {
        "id": str(uuid4()),
        "account_id": mapped["account_id"],
        "run_id": None,
        "channel": "simulated_chat",
        "direction": "outbound",
        "text": text,
        "status": "draft" if approved else "blocked",
        "version": 1,
        "citation_ids": [source.citation_id for source in result.sources],
        "created_at": now.isoformat(),
    }
    connection.execute(
        "INSERT INTO collection_messages VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            message["id"],
            message["account_id"],
            None,
            "simulated_chat",
            "outbound",
            text,
            message["status"],
            1,
            Jsonb(message["citation_ids"]),
            now,
        ),
    )
    template = template_binding(template)
    save_binding(connection, message["id"], evidence, template)
    if approved:
        attest_candidate(
            connection,
            message,
            policy,
            {**evidence, "template_binding": template},
            settings,
            now,
            "simulated",
        )
    append_event(
        connection,
        mapped["account_id"],
        "draft_created",
        "operator",
        message["status"],
        now,
        reason_codes=policy["reason_codes"] or screening["reason_codes"],
    )
    response = {
        "attempt_id": message["id"],
        "message_id": message["id"],
        "status": "APPROVED" if approved else "BLOCKED" if policy["allowed"] else "NOT_SENT",
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "decision": screening,
        "is_simulated": True,
    }
    record(connection, scope, str(key), payload, response)
    return response


def dispatch_legacy(connection, actor, prepared, secret, clock):
    from app.modules.conversations.dispatch import dispatch_message

    require(actor, "attempt:create")
    # Actual pre-cutover delivered attempts remain history, with their original
    # IDs. Capability and explicit mapping are still required before replay.
    historical = connection.execute(
        "SELECT * FROM trust.attempts WHERE id=%s AND principal=%s AND product=%s",
        (UUID(prepared["attempt_id"]), actor.principal, actor.product),
    ).fetchone()
    if historical:
        mapped_account(connection, actor, historical["case_id"])
        if historical["status"] == "DELIVERED":
            return {
                "attempt_id": str(historical["id"]),
                "status": "DELIVERED",
                "idempotent_replay": True,
                "is_simulated": True,
            }
        raise TrustError("legacy_attempt_requires_new_draft", 409)
    binding = connection.execute(
        "SELECT * FROM collection_message_bindings WHERE message_id=%s",
        (prepared["attempt_id"],),
    ).fetchone()
    if not binding:
        raise TrustError("legacy_case_mapping_required", 409)
    envelope = binding["evidence_references"][0]
    mapped = mapped_account(connection, actor, envelope["legacy_case_id"])
    message = connection.execute(
        "SELECT * FROM collection_messages WHERE id=%s", (prepared["attempt_id"],)
    ).fetchone()
    if message["account_id"] != mapped["account_id"]:
        raise TrustError("legacy_case_mapping_required", 409)
    if prepared.get("text") != message["text"]:
        return {
            "attempt_id": message["id"],
            "status": "NOT_SENT",
            "reason_codes": ["TEXT_HASH_MISMATCH"],
            "is_simulated": True,
        }
    settings = collection_settings(connection, secret)
    result = dispatch_message(
        connection,
        settings,
        message["id"],
        DispatchRequest(idempotency_key=UUID(message["id"]), expected_message_version=1),
        Caller(principal_id="local-operator", account_id=mapped["account_id"]),
        clock,
    )
    return {
        "attempt_id": message["id"],
        "status": "DELIVERED" if result["status"] == "simulated_sent" else "NOT_SENT",
        "reason_codes": result["reason_codes"],
        "is_simulated": True,
    }


def receive_legacy(connection, actor, case_id, text, now):
    from app.modules.reviews.service import open_review

    require(actor, "conversation:write")
    mapped = mapped_account(connection, actor, case_id)
    lock_case(connection, mapped["account_id"])
    if not isinstance(text, str) or not 1 <= len(text) <= 1000:
        raise TrustError("INVALID_MESSAGE", 422)
    categories = inbound_categories(text)
    if categories:
        kind = "dispute" if "dispute" in categories else "hardship"
        open_review(connection, mapped["account_id"], kind, now)
    if {"stop_request", "third_party_responder"} & set(categories):
        connection.execute(
            "UPDATE collection_authorities SET opted_out=true WHERE account_id=%s",
            (mapped["account_id"],),
        )
    message_id = str(uuid4())
    connection.execute(
        "INSERT INTO collection_messages VALUES (%s,%s,NULL,'simulated_chat','inbound',%s,"
        "'received',1,%s,%s)",
        (message_id, mapped["account_id"], text, Jsonb([]), now),
    )
    append_event(
        connection,
        mapped["account_id"],
        "message_received",
        "operator",
        "received",
        now,
        reason_codes=categories,
    )
    return {
        "id": message_id,
        "categories": categories,
        "case_paused": bool(categories),
        "is_synthetic": True,
    }


def read_legacy(connection, actor, case_id):
    require(actor, "conversation:read")
    mapped = mapped_account(connection, actor, case_id)
    from app.modules.conversations.redaction import sanitize_conversation

    rows = connection.execute(
        "SELECT id,text AS body,created_at FROM collection_messages WHERE account_id=%s "
        "ORDER BY created_at,id LIMIT 100",
        (mapped["account_id"],),
    ).fetchall()
    for row in rows:
        row["context_redacted"] = sanitize_conversation(row["body"])
        row["categories"] = []
    # Retain the original legacy raw history for authorized officers only.
    legacy = connection.execute(
        "SELECT id,body,context_redacted,categories,created_at FROM trust.conversations "
        "WHERE case_id=%s AND principal=%s AND product=%s ORDER BY created_at,id LIMIT 100",
        (UUID(str(case_id)), actor.principal, actor.product),
    ).fetchall()
    return sorted(rows + legacy, key=lambda row: (row["created_at"], str(row["id"])))[:100]
