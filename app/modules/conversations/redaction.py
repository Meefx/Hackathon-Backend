"""Minimized untrusted conversation context, kept outside the knowledge corpus."""

import re

from app.shared.content import (
    AMOUNT_PATTERN,
    EMAIL_PATTERN,
    LABELED_IDENTIFIER_PATTERN,
    LONG_IDENTIFIER_PATTERN,
    MAX_CONVERSATION_CHARS,
    PHONE_PATTERN,
    SECRET_PATTERN,
    ContentValidationError,
    normalize_content,
)
from app.trust.errors import TrustError


def sanitize_conversation(text: str, identifiers: tuple[str, ...] = ()) -> str:
    """Produce minimized untrusted model context; preserve the original separately."""
    try:
        normalized = normalize_content(text, MAX_CONVERSATION_CHARS)
    except ContentValidationError as exc:
        raise TrustError(exc.code, status=422) from exc
    if not isinstance(identifiers, tuple) or any(
        not isinstance(value, str) for value in identifiers
    ):
        raise TrustError("IDENTIFIERS_INVALID", status=422)
    for identifier in sorted(
        {value for value in identifiers if value.strip()}, key=len, reverse=True
    ):
        normalized = re.sub(re.escape(identifier), "[IDENTIFIER]", normalized, flags=re.IGNORECASE)
    normalized = SECRET_PATTERN.sub("[SECRET]", normalized)
    for pattern in (
        EMAIL_PATTERN,
        PHONE_PATTERN,
        LONG_IDENTIFIER_PATTERN,
        LABELED_IDENTIFIER_PATTERN,
    ):
        normalized = pattern.sub("[IDENTIFIER]", normalized)
    normalized = AMOUNT_PATTERN.sub("[AMOUNT]", normalized)
    normalized = re.sub(r"\d+(?:[.,:/-]\d+)*", "[NUMBER]", normalized)
    return normalized
