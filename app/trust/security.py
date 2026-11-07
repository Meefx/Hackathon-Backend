"""Compatibility imports for the canonical producer authorization."""

from app.modules.knowledge.authorization import (
    TOKEN_AUDIENCE,
    TOKEN_ISSUER,
    TOKEN_TTL_SECONDS,
    Actor,
    decode_token,
    issue_token,
    require,
    require_tool,
)

__all__ = [
    "TOKEN_AUDIENCE",
    "TOKEN_ISSUER",
    "TOKEN_TTL_SECONDS",
    "Actor",
    "decode_token",
    "issue_token",
    "require",
    "require_tool",
]
