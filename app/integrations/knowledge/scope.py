"""Resolve a read-only KMS role from trusted account authority and execution assignment."""

from datetime import datetime

from app.config import get_settings
from app.modules.knowledge.authorization import Actor, require
from app.shared.errors import DomainError


def resolve_actor(connection, caller, at: datetime) -> Actor:
    if not caller.account_id or at.tzinfo is None or at.utcoffset() is None:
        raise DomainError("knowledge_scope_denied", "Knowledge account scope is required", 403)
    kind = caller.actor_kind
    expected = {
        "operator": "local-operator",
        "worker": "local-worker",
        "agent": get_settings().mcp_principal_id,
    }
    if kind not in expected or caller.principal_id != expected[kind]:
        raise DomainError("knowledge_scope_denied", "Knowledge caller scope is invalid", 403)
    if kind != "operator":
        from app.modules.runs.service import authorize_assignment

        authorize_assignment(connection, caller, caller.account_id, caller.run_id, at)
    from app.modules.compliance.service import get_authority

    authority = get_authority(connection, caller.account_id)
    if not authority:
        raise DomainError("knowledge_scope_denied", "Knowledge authority scope is unavailable", 403)
    actor = Actor(
        caller.principal_id,
        "operator" if kind == "operator" else "personal",
        authority["principal"],
        authority["product"],
    )
    require(actor, "knowledge:read")
    return actor
