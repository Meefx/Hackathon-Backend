"""Deterministic PDF text and curated provision extraction, without model rewriting.

A page window is curator-supplied evidence scope, not proof of legal completeness.
Image-only PDFs require a separately reviewed OCR sidecar and retain raw-byte hash.
"""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from pypdf import PdfReader

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 500
MAX_CHARS = 2_000_000
MAX_PAGE_CONTENT_BYTES = 4 * 1024 * 1024
Section = Literal["body", "explanation"]
_ARTICLE = re.compile(r"(?im)^[ \t]*Pasal[ \t]+([0-9]{1,4})([A-Za-z]?)[ \t]*[.]?[ \t]*(?=\r?$)")
_EXPLANATION = re.compile(
    r"(?im)^[ \t]*PENJELASAN[ \t]*(?:\r?\n|[ \t]+)ATAS\b|"
    r"^[ \t]*(?:II[.][ \t]*)?PASAL[ \t]+DEMI[ \t]+PASAL[ \t]*$"
)


class SourceExtractionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ExtractedPage:
    number: int
    text: str


@dataclass(frozen=True)
class ExtractionResult:
    pages: tuple[ExtractedPage, ...]
    sha256: str
    empty_pages: tuple[int, ...]


@dataclass(frozen=True)
class ProvisionResult:
    text: str
    page_start: int
    page_end: int
    article: int | None
    section: Section
    sha256: str


def _hash(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_pdf(
    body: bytes, *, max_pages: int = MAX_PAGES, max_chars: int = MAX_CHARS
) -> ExtractionResult:
    """Extract numbered physical PDF pages; do not silently OCR or decrypt.

    Byte/page/content/text bounds reduce ingestion exposure. They do not constitute
    a sandbox for every malicious PDF; run ingestion in a resource-limited worker
    before accepting arbitrary, untrusted uploads in a production deployment.
    """
    if not isinstance(body, bytes) or not body.startswith(b"%PDF-") or len(body) > MAX_BYTES:
        raise SourceExtractionError("PDF_INVALID")
    if (
        type(max_pages) is not int
        or not 1 <= max_pages <= MAX_PAGES
        or type(max_chars) is not int
        or not 1 <= max_chars <= MAX_CHARS
    ):
        raise SourceExtractionError("PDF_EXTRACTION_CONFIGURATION_INVALID")
    try:
        reader = PdfReader(BytesIO(body), strict=True)
        if reader.is_encrypted:
            raise SourceExtractionError("PDF_ENCRYPTED")
        if not 1 <= len(reader.pages) <= max_pages:
            raise SourceExtractionError("PDF_PAGE_LIMIT")
        pages: list[ExtractedPage] = []
        empty: list[int] = []
        length = 0
        for number, page in enumerate(reader.pages, start=1):
            content = page.get_contents()
            if content is not None and len(content.get_data()) > MAX_PAGE_CONTENT_BYTES:
                raise SourceExtractionError("PDF_CONTENT_LIMIT")
            text = page.extract_text() or ""
            length += len(text)
            if length > max_chars:
                raise SourceExtractionError("PDF_TEXT_LIMIT")
            if not text.strip():
                empty.append(number)
            pages.append(ExtractedPage(number, text))
        if len(empty) == len(pages):
            raise SourceExtractionError("SCAN_REQUIRES_REVIEW")
        digest = _hash([{"number": page.number, "text": page.text} for page in pages])
        return ExtractionResult(tuple(pages), digest, tuple(empty))
    except SourceExtractionError:
        raise
    except Exception as exc:
        raise SourceExtractionError("PDF_INVALID") from exc


def extract_provision(
    pages: Sequence[ExtractedPage],
    page_start: int,
    page_end: int,
    article: int | None = None,
    section: Section = "body",
) -> ProvisionResult:
    """Select exact extracted text within a curator-reviewed physical page window.

    Pass the complete extracted page sequence so explanation markers before the
    window remain visible. Repeated target headers within one section/window are
    ambiguous and rejected. Numbered articles and explanatory text never merge.
    """
    if (
        type(page_start) is not int
        or type(page_end) is not int
        or not 1 <= page_start <= page_end
        or section not in ("body", "explanation")
        or (article is not None and (type(article) is not int or not 1 <= article <= 9999))
        or not pages
        or len(pages) > MAX_PAGES
    ):
        raise SourceExtractionError("PROVISION_WINDOW_INVALID")
    if any(
        not isinstance(page, ExtractedPage)
        or type(page.number) is not int
        or page.number != index
        or not isinstance(page.text, str)
        for index, page in enumerate(pages, start=1)
    ) or page_end > len(pages):
        raise SourceExtractionError("PROVISION_WINDOW_INVALID")
    if sum(len(page.text) for page in pages) > MAX_CHARS:
        raise SourceExtractionError("PDF_TEXT_LIMIT")
    pieces: list[str] = []
    starts: list[int] = []
    offset = 0
    for page in pages:
        starts.append(offset)
        pieces.append(page.text)
        offset += len(page.text) + 1
    whole = "\n".join(pieces)
    lower = starts[page_start - 1]
    upper = starts[page_end] if page_end < len(starts) else len(whole)
    if not whole[lower:upper].strip():
        raise SourceExtractionError("SCAN_REQUIRES_REVIEW")
    explanation = _EXPLANATION.search(whole)
    if section == "body":
        if explanation is not None:
            upper = min(upper, explanation.start())
    else:
        if explanation is None:
            raise SourceExtractionError("PROVISION_SECTION_NOT_FOUND")
        lower = max(lower, explanation.start())
    if lower >= upper:
        raise SourceExtractionError("PROVISION_SECTION_NOT_FOUND")
    selected = whole[lower:upper]
    if article is not None:
        headers = list(_ARTICLE.finditer(selected))
        matches = [
            (index, header)
            for index, header in enumerate(headers)
            if int(header[1]) == article and not header[2]
        ]
        if not matches:
            raise SourceExtractionError("PROVISION_NOT_FOUND")
        if len(matches) != 1:
            raise SourceExtractionError("PROVISION_AMBIGUOUS")
        index, header = matches[0]
        begin = lower + header.start()
        upper = lower + headers[index + 1].start() if index + 1 < len(headers) else upper
        lower = begin
    raw = whole[lower:upper]
    text = raw.strip()
    if not text:
        raise SourceExtractionError("SCAN_REQUIRES_REVIEW")
    first = lower + len(raw) - len(raw.lstrip())
    last = upper - (len(raw) - len(raw.rstrip())) - 1
    physical_start = bisect_right(starts, first)
    physical_end = bisect_right(starts, last)
    digest = _hash(
        {
            "text": text,
            "page_start": physical_start,
            "page_end": physical_end,
            "article": article,
            "section": section,
        }
    )
    return ProvisionResult(text, physical_start, physical_end, article, section, digest)
