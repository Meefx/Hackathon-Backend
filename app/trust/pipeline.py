"""Compatibility names for explicitly mapped canonical collection operations."""

from app.modules.conversations.compatibility import prepare_legacy
from app.trust.errors import TrustError


def prepare(conn, actor, case_id, template_id, idempotency_key, secret, clock, layer2):
    return prepare_legacy(conn, actor, case_id, template_id, idempotency_key, secret, clock, layer2)


def execute(conn, actor, case_id, template_id, key, secret, clock, layer2):
    from app.services.dispatch_gate import dispatch

    result = prepare(conn, actor, case_id, template_id, key, secret, clock, layer2)
    if result["status"] == "APPROVED":
        result = dispatch(conn, actor, result, secret, clock)
    elif result["status"] not in {"DELIVERED", "BLOCKED", "NOT_SENT"}:
        raise TrustError("legacy_attempt_requires_new_draft", 409)
    public_fields = {
        "attempt_id",
        "status",
        "is_simulated",
        "dispatch_id",
        "idempotent_replay",
        "reason_codes",
        "decision",
        "message",
    }
    return {name: value for name, value in result.items() if name in public_fields}
