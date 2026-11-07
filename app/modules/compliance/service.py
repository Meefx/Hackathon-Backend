import hashlib
import hmac
from datetime import UTC, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.modules.cases.repository import contact_state, lock_case
from app.modules.compliance import repository
from app.shared.idempotency import digest
from app.trust.safety import ZONES, evaluate_contact


def evaluate_contact_policy(
    connection, account_id, now, check_frequency=True, exclude_message_id=None
):
    case = lock_case(connection, account_id)
    policy = connection.execute("SELECT * FROM collection_policies WHERE active").fetchone()
    current = repository.authority(connection, account_id)
    reasons = []
    state = contact_state(connection, case)
    if not policy:
        reasons.append("policy_missing")
    if not current:
        reasons.append("contact_authority_missing")
    else:
        if (current["principal"], current["product"]) not in {
            ("cf", "spaylater"),
            ("ldn", "spinjam"),
        }:
            reasons.append("authority_scope_invalid")
        if not current["mandate_active"]:
            reasons.append("mandate_unverified")
        history = (
            repository.contact_history(connection, current, exclude_message_id)
            if check_frequency
            else []
        )
        context = {
            "is_synthetic": current["is_synthetic"],
            "timezone": current["borrower_timezone"],
            "borrower_owned": current["borrower_owned"],
            "verified": current["verified"],
            "opted_out": current["opted_out"],
            "data_verified_at": current["data_verified_at"],
            "calendar_years": current["calendar_years"],
            "holidays": current["holidays"],
            "channel": "sim_chat",
            "purpose": "proactive",
            # Local holds have a separate canonical reason. Evaluate the other
            # rules fully so an explicit resume still checks calendar and budget.
            "state": "active" if state in {"paused_operator", "paused_human_review"} else state,
            "proactive_history": history,
        }
        # Feed ISO dates to the shared deterministic policy contract.
        context["proactive_history"] = [item.isoformat() for item in history]
        native = evaluate_contact(context, now)
        reasons.extend(code.lower() for code in native["reason_codes"])
        if policy and current["borrower_timezone"] in ZONES:
            local = now.astimezone(ZoneInfo(current["borrower_timezone"]))
            if local.date() > policy["coverage_until"]:
                reasons.append("holiday_coverage_missing")
            if not policy["contact_start_hour"] <= local.hour < policy["contact_end_hour"]:
                reasons.append("outside_contact_window")
            daily = sum(item.astimezone(local.tzinfo).date() == local.date() for item in history)
            if check_frequency and daily >= policy["maximum_daily_messages"]:
                reasons.append("frequency_limit")
    if not case["eligible"]:
        reasons.append("account_ineligible")
    if not case["recipient_verified"] or not case["recipient_id"]:
        reasons.append("recipient_unverified")
    if state != "active":
        reasons.append("case_paused")
    if repository.legacy_holds(connection, account_id):
        reasons.extend(["case_paused", "legacy_case_paused"])
    return {
        "account_id": account_id,
        "case_version": case["version"],
        "allowed": not reasons,
        "reason_codes": list(dict.fromkeys(reasons)),
        "policy_version": policy["version"] if policy else None,
        "authority_hash": digest(current) if current else None,
        "contact_state": state,
        "evaluated_at": now.isoformat(),
    }


def binding_for(message, case, decision, knowledge_binding, expires_at):
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ValueError("Approval expiry requires a timezone")
    return {
        **({"channel": message["channel"]} if message["channel"] in {"whatsapp", "email"} else {}),
        "message_id": message["id"],
        "message_version": message["version"],
        "content_hash": digest(message["text"]),
        "account_id": message["account_id"],
        "recipient_id": case["recipient_id"],
        "case_version": case["version"],
        "policy_version": decision["policy_version"],
        "authority_hash": decision.get("authority_hash"),
        "template_binding": knowledge_binding.get("template_binding"),
        "snapshot_hash": knowledge_binding["snapshot_hash"],
        "knowledge_references": knowledge_binding["references"],
        "citation_ids": message["citation_ids"],
        # PostgreSQL returns timestamptz in the session timezone. Bind the instant
        # canonically so the same expiry survives a database round trip unchanged.
        "expires_at": expires_at.astimezone(UTC).isoformat(),
    }


def sign_binding(binding, secret):
    return hmac.new(secret.encode(), digest(binding).encode(), hashlib.sha256).hexdigest()


def issue_approval(connection, message, decision, knowledge_binding, settings, now, mode):
    if not decision["allowed"] or mode != "simulated":
        return False
    case = lock_case(connection, message["account_id"])
    expiry = now + timedelta(seconds=min(settings.approval_ttl_seconds, 120))
    binding = binding_for(message, case, decision, knowledge_binding, expiry)
    guardrail = {
        "decision": "allow",
        "classifier_mode": "demo_fixture",
        "policy_version": decision["policy_version"],
        "evaluated_at": now.isoformat(),
    }
    connection.execute(
        "INSERT INTO message_approvals VALUES (%s,%s,%s,%s,%s)",
        (
            message["id"],
            sign_binding(binding, settings.approval_hmac_secret.get_secret_value()),
            Jsonb(binding),
            expiry,
            Jsonb(guardrail),
        ),
    )
    return True


def get_authority(connection: Connection[dict[str, Any]], account_id: str) -> dict:
    from app.shared.errors import DomainError

    current = repository.authority(connection, account_id)
    if not current:
        raise DomainError("contact_authority_missing", "Account authority is required", 403)
    return current
