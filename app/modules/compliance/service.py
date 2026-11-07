import hashlib
import hmac
from datetime import timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from app.modules.cases.repository import contact_state, lock_case
from app.shared.idempotency import digest


def evaluate_contact_policy(connection, account_id, now, check_frequency=True):
    case = lock_case(connection, account_id)
    policy = connection.execute("SELECT * FROM collection_policies WHERE active").fetchone()
    reasons = []
    state = contact_state(connection, case)
    if not policy:
        reasons.append("policy_missing")
    else:
        local = now.astimezone(ZoneInfo("Asia/Jakarta"))
        if local.date() > policy["coverage_until"]:
            reasons.append("holiday_coverage_missing")
        if not policy["contact_start_hour"] <= local.hour < policy["contact_end_hour"]:
            reasons.append("outside_contact_window")
        if check_frequency:
            count = connection.execute(
                "SELECT count(*) AS n FROM dispatch_receipts WHERE "
                "account_id=%s AND (created_at AT TIME ZONE 'Asia/Jakarta')::date=%s",
                (account_id, local.date()),
            ).fetchone()["n"]
            if count >= policy["maximum_daily_messages"]:
                reasons.append("frequency_limit")
    if not case["eligible"]:
        reasons.append("account_ineligible")
    if not case["recipient_verified"] or not case["recipient_id"]:
        reasons.append("recipient_unverified")
    if state != "active":
        reasons.append("case_paused")
    return {
        "account_id": account_id,
        "case_version": case["version"],
        "allowed": not reasons,
        "reason_codes": reasons,
        "policy_version": policy["version"] if policy else None,
        "contact_state": state,
        "evaluated_at": now.isoformat(),
    }


def binding_for(message, case, decision, knowledge_binding, expires_at):
    return {
        "message_id": message["id"],
        "message_version": message["version"],
        "content_hash": digest(message["text"]),
        "account_id": message["account_id"],
        "recipient_id": case["recipient_id"],
        "case_version": case["version"],
        "policy_version": decision["policy_version"],
        "snapshot_hash": knowledge_binding["snapshot_hash"],
        "knowledge_references": knowledge_binding["references"],
        "citation_ids": message["citation_ids"],
        "expires_at": expires_at.isoformat(),
    }


def sign_binding(binding, secret):
    return hmac.new(secret.encode(), digest(binding).encode(), hashlib.sha256).hexdigest()


def issue_approval(connection, message, decision, knowledge_binding, settings, now, mode):
    if not decision["allowed"] or mode != "simulated":
        return False
    case = lock_case(connection, message["account_id"])
    expiry = now + timedelta(seconds=settings.approval_ttl_seconds)
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
