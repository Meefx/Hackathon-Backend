"""Bounded, deterministic ingestion checks and model-context minimization.

These checks reject known unsafe inputs. They are not proof of legal correctness
or a replacement for approval and scoped retrieval.
"""

import html
import re
import unicodedata

from app.trust.errors import TrustError

MAX_KNOWLEDGE_CHARS = 100_000
MAX_CONVERSATION_CHARS = 10_000
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\w)(?:\+?62[\s().-]*8|08)(?:[\s().-]*\d){7,12}(?!\d)")
_LONG_IDENTIFIER = re.compile(r"(?<![\w])\d(?:[ -]?\d){12,18}(?![\w])")
_LABELED_IDENTIFIER = re.compile(
    r"\b(?:nik|ktp|passport|account\s*(?:number|no)|nomor\s*(?:rekening|telepon|hp))"
    r"\s*[:=]\s*[A-Z0-9][A-Z0-9 .-]{5,30}",
    re.IGNORECASE,
)
_SECRETS = re.compile(
    r"(?:\b(?:sk-(?:proj-)?|gsk_|xox[baprs]-)[A-Za-z0-9_-]{12,}"
    r"|\bAKIA[A-Z0-9]{16}\b"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|\b(?:api[_ -]?key|client[_ -]?secret|password|access[_ -]?token)"
    r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{8,})",
    re.IGNORECASE,
)
_INJECTION = re.compile(
    r"(?:\b(?:ignore|disregard|override|forget)\s+(?:all\s+)?(?:the\s+)?"
    r"(?:(?:previous|prior|above|system|developer|safety)\s+)+(?:instructions?|rules?|prompts?|policy)"
    r"|\b(?:abaikan|lupakan)\s+(?:(?:semua|seluruh)\s+)?(?:instruksi|aturan|kebijakan)"
    r"|\b(?:reveal|print|dump|expose)\s+(?:(?:the|your)\s+)?(?:system|developer)\s+prompt"
    r"|<\s*/?\s*(?:system|developer|tool)\s*>"
    r"|\[\s*/?\s*INST\s*\]"
    r"|(?:^|\n)\s*(?:SYSTEM|DEVELOPER|ASSISTANT|TOOL)\s*:"
    r"|[\"']role[\"']\s*:\s*[\"'](?:system|developer|tool)[\"'])",
    re.IGNORECASE,
)
_AMOUNT = re.compile(
    r"(?:(?:\b(?:rp|idr|usd|rupiah)\.?\s*|[$€£])\d[\d.,]*(?:\s*(?:million|billion|juta|ribu))?"
    r"|\b\d[\d.,]*\s*(?:rupiah|idr|usd|dollars?|persen|percent)\b"
    r"|\b\d[\d.,]*\s*%"
    r"|\b(?:satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh|seratus|seribu)"
    r"(?:\s+(?:puluh|belas|ratus|ribu|juta|miliar|rupiah))+\b)",
    re.IGNORECASE,
)
_MARKDOWN_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_LEGAL_HEADING = re.compile(
    r"^\s*((?:Article|Pasal|Section|Bab)\s+[A-Z0-9][\w.()-]*[^\n]{0,100})$", re.IGNORECASE
)
_PARAGRAPH_HEADING = re.compile(r"^\s*((?:Paragraph|Ayat)\s*\(?\d+\)?[^\n]{0,80})$", re.IGNORECASE)


def _normalize(text: str, maximum: int) -> str:
    if not isinstance(text, str) or not text.strip():
        raise TrustError("CONTENT_INVALID", status=422)
    if len(text) > maximum:
        raise TrustError("CONTENT_TOO_LARGE", status=422)
    normalized = unicodedata.normalize("NFKC", html.unescape(text))
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Cf")
    if any(unicodedata.category(char) == "Cc" and char not in "\n\r\t" for char in normalized):
        raise TrustError("CONTENT_INVALID", status=422)
    if len(normalized) > maximum:
        raise TrustError("CONTENT_TOO_LARGE", status=422)
    return normalized.replace("\r\n", "\n").replace("\r", "\n")


def validate_knowledge(text: str) -> None:
    """Reject detected PII, credentials, hostile instructions and oversized input."""
    normalized = _normalize(text, MAX_KNOWLEDGE_CHARS)
    if _SECRETS.search(normalized):
        raise TrustError("KNOWLEDGE_SECRET_DETECTED", status=422)
    if any(
        pattern.search(normalized)
        for pattern in (_EMAIL, _PHONE, _LONG_IDENTIFIER, _LABELED_IDENTIFIER)
    ):
        raise TrustError("KNOWLEDGE_PII_DETECTED", status=422)
    if _INJECTION.search(normalized):
        raise TrustError("KNOWLEDGE_INJECTION_DETECTED", status=422)


def _split_bounded(text: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    remaining = text.strip()
    while len(remaining) > max_chars:
        boundary = remaining.rfind("\n\n", 0, max_chars + 1)
        if boundary < max_chars // 2:
            boundary = remaining.rfind(" ", 0, max_chars + 1)
        if boundary <= 0:
            boundary = max_chars
        pieces.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def chunk_sections(text: str, max_chars: int = 1800) -> list[dict]:
    """Chunk by headings/articles and preserve structural parent locators.

    Part locators refer to their logical section; retrieval can expand all parts
    and ancestors instead of treating an isolated legal sentence as authority.
    """
    if type(max_chars) is not int or not 100 <= max_chars <= 10_000:
        raise TrustError("CHUNK_SIZE_INVALID", status=422)
    normalized = _normalize(text, MAX_KNOWLEDGE_CHARS)
    sections: list[tuple[str, str | None, str]] = []
    stack: list[tuple[int, str]] = []
    locator, parent = "Preamble", None
    lines: list[str] = []
    seen: dict[str, int] = {}
    for line in normalized.splitlines():
        markdown = _MARKDOWN_HEADING.match(line)
        legal = _LEGAL_HEADING.match(line)
        paragraph = _PARAGRAPH_HEADING.match(line)
        if markdown or legal or paragraph:
            if lines and "\n".join(lines).strip():
                sections.append((locator, parent, "\n".join(lines).strip()))
            if markdown:
                level, heading = len(markdown[1]), markdown[2].strip()
            elif legal:
                level, heading = 1, legal[1].strip()
            else:
                assert paragraph is not None
                level, heading = 2, paragraph[1].strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else None
            base_locator = f"{parent} / {heading}" if parent else heading
            seen[base_locator] = seen.get(base_locator, 0) + 1
            locator = (
                base_locator
                if seen[base_locator] == 1
                else f"{base_locator} [{seen[base_locator]}]"
            )
            stack.append((level, locator))
            lines = [line.strip()]
        else:
            lines.append(line)
    if lines and "\n".join(lines).strip():
        sections.append((locator, parent, "\n".join(lines).strip()))
    chunks = []
    for section, section_parent, body in sections:
        parts = _split_bounded(body, max_chars)
        for index, part in enumerate(parts, start=1):
            chunks.append(
                {
                    "locator": section if len(parts) == 1 else f"{section} / Part {index}",
                    "text": part,
                    "parent_locator": section_parent if len(parts) == 1 else section,
                }
            )
    return chunks


def sanitize_conversation(text: str, identifiers: tuple[str, ...] = ()) -> str:
    """Produce minimized untrusted model context; preserve the original separately."""
    normalized = _normalize(text, MAX_CONVERSATION_CHARS)
    if not isinstance(identifiers, tuple) or any(
        not isinstance(value, str) for value in identifiers
    ):
        raise TrustError("IDENTIFIERS_INVALID", status=422)
    for identifier in sorted(
        {value for value in identifiers if value.strip()}, key=len, reverse=True
    ):
        normalized = re.sub(re.escape(identifier), "[IDENTIFIER]", normalized, flags=re.IGNORECASE)
    normalized = _SECRETS.sub("[SECRET]", normalized)
    for pattern in (_EMAIL, _PHONE, _LONG_IDENTIFIER, _LABELED_IDENTIFIER):
        normalized = pattern.sub("[IDENTIFIER]", normalized)
    normalized = _AMOUNT.sub("[AMOUNT]", normalized)
    normalized = re.sub(r"\d+(?:[.,:/-]\d+)*", "[NUMBER]", normalized)
    return normalized
