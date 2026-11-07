"""Deterministic text normalization and privacy patterns shared by ingestion and redaction."""

import html
import re
import unicodedata


class ContentValidationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


MAX_CONVERSATION_CHARS = 10_000

EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)

PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?62[\s().-]*8|08)(?:[\s().-]*\d){7,12}(?!\d)")

LONG_IDENTIFIER_PATTERN = re.compile(r"(?<![\w])\d(?:[ -]?\d){12,18}(?![\w])")

LABELED_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:nik|ktp|passport|account\s*(?:number|no)|nomor\s*(?:rekening|telepon|hp))"
    r"\s*[:=]\s*[A-Z0-9][A-Z0-9 .-]{5,30}",
    re.IGNORECASE,
)

SECRET_PATTERN = re.compile(
    r"(?:\b(?:sk-(?:proj-)?|gsk_|xox[baprs]-)[A-Za-z0-9_-]{12,}"
    r"|\bAKIA[A-Z0-9]{16}\b"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|\b(?:api[_ -]?key|client[_ -]?secret|password|access[_ -]?token)"
    r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{8,})",
    re.IGNORECASE,
)

AMOUNT_PATTERN = re.compile(
    r"(?:(?:\b(?:rp|idr|usd|rupiah)\.?\s*|[$€£])\d[\d.,]*(?:\s*(?:million|billion|juta|ribu))?"
    r"|\b\d[\d.,]*\s*(?:rupiah|idr|usd|dollars?|persen|percent)\b"
    r"|\b\d[\d.,]*\s*%"
    r"|\b(?:satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh|seratus|seribu)"
    r"(?:\s+(?:puluh|belas|ratus|ribu|juta|miliar|rupiah))+\b)",
    re.IGNORECASE,
)


def normalize_content(text: str, maximum: int) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ContentValidationError("CONTENT_INVALID")
    if len(text) > maximum:
        raise ContentValidationError("CONTENT_TOO_LARGE")
    normalized = unicodedata.normalize("NFKC", html.unescape(text))
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Cf")
    if any(unicodedata.category(char) == "Cc" and char not in "\n\r\t" for char in normalized):
        raise ContentValidationError("CONTENT_INVALID")
    if len(normalized) > maximum:
        raise ContentValidationError("CONTENT_TOO_LARGE")
    return normalized.replace("\r\n", "\n").replace("\r", "\n")
