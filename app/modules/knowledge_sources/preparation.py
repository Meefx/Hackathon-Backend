"""Curator-only file/network preparation, outside database transactions.

PDF bytes and extraction sidecars are independently pinned. OCR is never silently
upgraded to trusted text. The manifest is operator-controlled configuration.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from pypdf import PdfReader

from app.config import SOURCE_DOWNLOAD_HOSTS
from app.integrations.knowledge_sources.extract import (
    ExtractedPage,
    SourceExtractionError,
    extract_pdf,
)
from app.integrations.knowledge_sources.fetch import fetch_pdf
from app.modules.knowledge_sources.schemas import SourceSpec
from app.trust.audit import canonical
from app.trust.errors import TrustError

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "data" / "knowledge_sources"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def pages_hash(pages: tuple[ExtractedPage, ...]) -> str:
    return digest(canonical([{"number": p.number, "text": p.text} for p in pages]).encode())


@dataclass(frozen=True)
class PreparedSource:
    spec: SourceSpec
    body: bytes
    final_url: str
    pages: tuple[ExtractedPage, ...]
    extraction_sha256: str
    extraction_method: str
    extraction_review_required: bool


def validate_url(url: str) -> None:
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise TrustError("SOURCE_URL_FORBIDDEN", 422)
    parsed = urlparse(url)
    try:
        valid = parsed.port in (None, 443)
    except ValueError:
        valid = False
    if (
        not valid
        or parsed.scheme != "https"
        or parsed.hostname not in SOURCE_DOWNLOAD_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise TrustError("SOURCE_URL_FORBIDDEN", 422)


def validate_prepared(source: PreparedSource) -> None:
    validate_url(source.spec.source_url)
    validate_url(source.final_url)
    if (
        not source.body.startswith(b"%PDF-")
        or not 8 <= len(source.body) <= 20 * 1024 * 1024
        or not source.spec.sha256
        or digest(source.body) != source.spec.sha256
    ):
        raise TrustError("SOURCE_HASH_MISMATCH", 422)
    if (
        not 1 <= len(source.pages) <= 500
        or any(p.number != i for i, p in enumerate(source.pages, 1))
        or sum(len(p.text) for p in source.pages) > 2_000_000
        or pages_hash(source.pages) != source.extraction_sha256
    ):
        raise TrustError("EXTRACTION_HASH_MISMATCH", 422)
    if source.spec.extraction_sha256 and source.spec.extraction_sha256 != source.extraction_sha256:
        raise TrustError("EXTRACTION_HASH_MISMATCH", 422)
    whole = re.sub(r"\s+", " ", "\n".join(p.text for p in source.pages)).casefold()
    unextracted_scan = (
        source.extraction_method == "pdf_scan_no_text"
        and source.extraction_review_required is True
        and source.spec.extraction_sha256 is None
        and all(not page.text.strip() for page in source.pages)
    )
    if not unextracted_scan and any(
        re.sub(r"\s+", " ", phrase).casefold() not in whole
        for phrase in source.spec.expected_phrases
    ):
        raise TrustError("SOURCE_IDENTITY_MISMATCH", 422)
    keys = [p.key for p in source.spec.provisions]
    if len(set(keys)) != len(keys):
        raise TrustError("DUPLICATE_PROVISION", 422)


def _cache_path(value: str) -> Path:
    path = (ROOT / value).resolve()
    if not path.is_relative_to(CACHE.resolve()):
        raise TrustError("SOURCE_CACHE_PATH_FORBIDDEN", 422)
    return path


def prepare_source(spec: SourceSpec, *, use_cache: bool = False) -> PreparedSource:
    """Use verified local originals or fetch exact pinned bytes from curated hosts.

    Sidecars are local review artifacts, never fetched from a document-supplied URL.
    A changed publisher PDF fails its pin and requires a new manifest review.
    """
    validate_url(spec.source_url)
    final_url = str((spec.model_extra or {}).get("final_url", spec.source_url))
    if use_cache and spec.download_path and _cache_path(spec.download_path).is_file():
        path = _cache_path(spec.download_path)
        if path.stat().st_size > 20 * 1024 * 1024:
            raise TrustError("SOURCE_TOO_LARGE", 422)
        body = path.read_bytes()
    else:
        result = fetch_pdf(spec.source_url, allowed_hosts=SOURCE_DOWNLOAD_HOSTS)
        body, final_url = result.body, result.final_url
    if not spec.sha256 or digest(body) != spec.sha256:
        raise TrustError("SOURCE_HASH_MISMATCH", 422)
    method = "pypdf"
    review_required = spec.extraction_review_required
    if spec.pages_path and _cache_path(spec.pages_path).is_file():
        sidecar_path = _cache_path(spec.pages_path)
        if sidecar_path.stat().st_size > 12 * 1024 * 1024:
            raise TrustError("EXTRACTION_TOO_LARGE", 422)
        sidecar = json.loads(sidecar_path.read_text())
        if sidecar["source_sha256"] != spec.sha256 or not spec.extraction_sha256:
            raise TrustError("EXTRACTION_SOURCE_MISMATCH", 422)
        pages = tuple(ExtractedPage(p["number"], p["text"]) for p in sidecar["pages"])
        method = sidecar["extraction_method"]
        review_required = review_required or bool(sidecar["extraction_review_required"])
        # Sidecars must match the physical PDF, not just a manually declared page count.
        reader = PdfReader(BytesIO(body), strict=True)
        if reader.is_encrypted or len(reader.pages) != len(pages):
            raise TrustError("EXTRACTION_PAGE_MISMATCH", 422)
    else:
        try:
            result_text = extract_pdf(body)
            pages = result_text.pages
        except SourceExtractionError as exc:
            if exc.code != "SCAN_REQUIRES_REVIEW":
                raise
            # The raw hash is still mandatory. Absent OCR cannot establish identity
            # from text, and blank pages can never become reusable KMS provisions.
            reader = PdfReader(BytesIO(body), strict=True)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 500:
                raise SourceExtractionError("PDF_INVALID") from exc
            pages = tuple(ExtractedPage(number, "") for number in range(1, len(reader.pages) + 1))
            method = "pdf_scan_no_text"
            spec = spec.model_copy(update={"extraction_sha256": None})
        # Different extraction engines produce different bytes; require source review.
        review_required = True
        if spec.extraction_sha256 and pages_hash(pages) != spec.extraction_sha256:
            # No sidecar means the original pin cannot certify this new extraction.
            spec = spec.model_copy(update={"extraction_sha256": None})
    prepared = PreparedSource(
        spec, body, final_url, pages, pages_hash(pages), method, review_required
    )
    validate_prepared(prepared)
    return prepared
