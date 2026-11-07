"""Signed actor scopes and deny-by-default authorization for trust services."""

from dataclasses import dataclass
from datetime import datetime

import jwt

from app.trust.errors import TrustError

TOKEN_ISSUER = "collectflow-trust"
TOKEN_AUDIENCE = "collectflow-trust-api"
TOKEN_TTL_SECONDS = 8 * 60 * 60
_SCOPES = {("cf", "spaylater"), ("ldn", "spinjam")}
_ROLES = frozenset(
    {
        "knowledge",
        "qa",
        "personal",
        "process",
        "manager",
        "operator",
        "officer",
        "compliance",
        "admin",
    }
)
_CAPABILITIES = {
    "source:read": {"knowledge", "qa", "compliance", "admin"},
    "source:download": {"knowledge", "qa", "compliance", "admin"},
    "knowledge:ingest": {"knowledge", "admin"},
    "knowledge:read": _ROLES,
    "knowledge:qa": {"qa"},
    "knowledge:approve": {"compliance"},
    "knowledge:retire": {"compliance"},
    "template:create": {"knowledge", "admin"},
    "template:qa": {"qa"},
    "template:approve": {"compliance"},
    "case:create": {"operator", "admin"},
    "attempt:create": {"operator"},
    "audit:read": {"compliance", "qa", "admin"},
    "audit:verify": {"compliance"},
    "conversation:write": {"operator"},
    "conversation:read": {"officer", "compliance"},
    "safety:pause": {"qa", "compliance"},
}

# A model cannot gain capabilities by supplying a different purpose string.
# Only these exact tool/role/purpose combinations are exposed to an agent.
_AGENT_TOOLS = {
    ("knowledge", "search_knowledge", "research"): "knowledge:read",
    ("knowledge", "ingest_knowledge", "research"): "knowledge:ingest",
    ("personal", "search_knowledge", "borrower_response"): "knowledge:read",
    ("process", "search_knowledge", "improvement_analysis"): "knowledge:read",
    ("manager", "search_knowledge", "explanation"): "knowledge:read",
    ("qa", "search_knowledge", "compliance_review"): "knowledge:read",
    ("qa", "read_audit", "compliance_review"): "audit:read",
    ("qa", "pause_unsafe_activity", "safety_response"): "safety:pause",
}


@dataclass(frozen=True)
class Actor:
    subject: str
    role: str
    principal: str
    product: str


def _valid_actor(actor: Actor) -> bool:
    return (
        isinstance(actor, Actor)
        and isinstance(actor.subject, str)
        and 0 < len(actor.subject.strip()) <= 200
        and actor.subject == actor.subject.strip()
        and not any(ord(char) < 32 for char in actor.subject)
        and isinstance(actor.role, str)
        and actor.role in _ROLES
        and isinstance(actor.principal, str)
        and isinstance(actor.product, str)
        and (actor.principal, actor.product) in _SCOPES
    )


def _timestamp(now: datetime) -> int:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise TrustError("AUTH_CLOCK_INVALID", status=503)
    return int(now.timestamp())


def _validate_secret(secret: str) -> None:
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise TrustError("AUTH_CONFIGURATION_INVALID", status=503)


def issue_token(actor: Actor, secret: str, now: datetime) -> str:
    """Issue a scoped token after server-side authentication, never from request roles."""
    _validate_secret(secret)
    if not _valid_actor(actor):
        raise TrustError("ACTOR_SCOPE_INVALID", status=403)
    issued_at = _timestamp(now)
    return jwt.encode(
        {
            "sub": actor.subject,
            "role": actor.role,
            "principal": actor.principal,
            "product": actor.product,
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
            "iat": issued_at,
            "nbf": issued_at,
            "exp": issued_at + TOKEN_TTL_SECONDS,
        },
        secret,
        algorithm="HS256",
        headers={"typ": "JWT"},
    )


def decode_token(token: str, secret: str, now: datetime) -> Actor:
    """Verify signature, exact scope and time using the injectable application clock."""
    _validate_secret(secret)
    current = _timestamp(now)
    if not isinstance(token, str) or len(token) > 4096:
        raise TrustError("TOKEN_INVALID", status=401)
    try:
        header = jwt.get_unverified_header(token)
        if header != {"alg": "HS256", "typ": "JWT"}:
            raise ValueError("Unsupported header")
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
            options={
                "require": [
                    "sub",
                    "role",
                    "principal",
                    "product",
                    "iss",
                    "aud",
                    "iat",
                    "nbf",
                    "exp",
                ],
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
                "strict_aud": True,
            },
        )
        issued, valid_from, expires = (claims[key] for key in ("iat", "nbf", "exp"))
        if any(type(value) is not int for value in (issued, valid_from, expires)):
            raise ValueError("Invalid time claim")
        if not (
            issued <= current
            and valid_from == issued
            and current < expires
            and 0 < expires - issued <= TOKEN_TTL_SECONDS
        ):
            raise ValueError("Token not current")
        actor = Actor(
            subject=claims["sub"],
            role=claims["role"],
            principal=claims["principal"],
            product=claims["product"],
        )
        if not _valid_actor(actor):
            raise ValueError("Invalid actor")
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise TrustError("TOKEN_INVALID", status=401) from None
    return actor


def require(actor: Actor, capability: str) -> None:
    """Authorize exact capability; admin is not a universal safety override."""
    if (
        not _valid_actor(actor)
        or not isinstance(capability, str)
        or actor.role not in _CAPABILITIES.get(capability, ())
    ):
        raise TrustError("CAPABILITY_DENIED", status=403)


def require_tool(actor: Actor, tool: str, purpose: str) -> None:
    """Enforce static role and purpose allowlists before any tool execution."""
    if not _valid_actor(actor) or not isinstance(tool, str) or not isinstance(purpose, str):
        raise TrustError("CAPABILITY_DENIED", status=403)
    capability = _AGENT_TOOLS.get((actor.role, tool, purpose))
    if capability is None:
        raise TrustError("TOOL_DENIED", status=403)
    require(actor, capability)
