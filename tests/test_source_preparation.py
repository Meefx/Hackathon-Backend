"""Pinned raw scans can be archived without silently trusting unavailable OCR."""

import json

import pytest

from app.integrations.knowledge_sources.extract import SourceExtractionError, extract_pdf
from app.integrations.knowledge_sources.fetch import FetchResult
from app.modules.knowledge_sources import preparation
from app.modules.knowledge_sources.preparation import digest, pages_hash, prepare_source
from app.modules.knowledge_sources.schemas import SourceSpec
from app.trust.errors import TrustError
from tests.test_source_extract import make_pdf


@pytest.fixture
def source_cache(tmp_path, monkeypatch):
    cache = tmp_path / "data" / "knowledge_sources"
    cache.mkdir(parents=True)
    monkeypatch.setattr(preparation, "ROOT", tmp_path)
    monkeypatch.setattr(preparation, "CACHE", cache)
    return cache


def spec_for(body, **changes):
    values = {
        "key": "synthetic-pinned-source",
        "title": "Synthetic pinned public source",
        "publisher": "Synthetic publisher",
        "source_class": "primary",
        "source_url": "https://www.ojk.go.id/synthetic-pinned-source.pdf",
        "source_version": "synthetic-v1",
        "legal_status": "in_force",
        "effective_from": "2026-01-01",
        "scopes": [{"principal": "cf", "product": "spaylater"}],
        "sha256": digest(body),
        "download_path": "data/knowledge_sources/source.pdf",
        "pages_path": "data/knowledge_sources/absent-sidecar.json",
        "expected_phrases": ["Expected official document title"],
        "extraction_sha256": "a" * 64,
        "provisions": [
            {
                "key": "selected-page",
                "locator": "Physical PDF page 1",
                "title": "Synthetic selected page",
                "page_start": 1,
                "page_end": 1,
                "legal_status": "in_force",
                "effective_from": "2026-01-01",
            }
        ],
    }
    values.update(changes)
    return SourceSpec.model_validate(values)


@pytest.mark.parametrize("cached", [True, False])
def test_scan_without_sidecar_preserves_pin_and_requires_review(source_cache, monkeypatch, cached):
    body = make_pdf(["", ""])
    (source_cache / "source.pdf").write_bytes(body)
    spec = spec_for(body)
    calls = []

    def fake_fetch(url, **kwargs):
        calls.append(url)
        return FetchResult(url, url, "application/pdf", body, digest(body))

    monkeypatch.setattr(preparation, "fetch_pdf", fake_fetch)
    result = prepare_source(spec, use_cache=cached)
    assert result.body == body
    assert result.spec.sha256 == digest(body)
    assert result.extraction_method == "pdf_scan_no_text"
    assert result.extraction_review_required is True
    assert result.spec.extraction_sha256 is None
    assert [page.number for page in result.pages] == [1, 2]
    assert [page.text for page in result.pages] == ["", ""]
    assert result.extraction_sha256 == pages_hash(result.pages)
    assert calls == ([] if cached else [spec.source_url])


def test_scan_bypass_cannot_hide_ordinary_identity_mismatch(source_cache):
    body = make_pdf(["Pasal 62\nUnrelated synthetic document."])
    (source_cache / "source.pdf").write_bytes(body)
    with pytest.raises(TrustError, match="SOURCE_IDENTITY_MISMATCH"):
        prepare_source(spec_for(body), use_cache=True)


def test_forged_scan_label_with_nonempty_text_does_not_bypass_identity(source_cache):
    body = make_pdf(["Unrelated synthetic text."])
    (source_cache / "source.pdf").write_bytes(body)
    result = extract_pdf(body)
    prepared = preparation.PreparedSource(
        spec_for(body, extraction_sha256=None),
        body,
        "https://www.ojk.go.id/synthetic-pinned-source.pdf",
        result.pages,
        pages_hash(result.pages),
        "pdf_scan_no_text",
        True,
    )
    with pytest.raises(TrustError, match="SOURCE_IDENTITY_MISMATCH"):
        preparation.validate_prepared(prepared)


def test_invalid_pdf_is_not_reclassified_as_a_scan(source_cache):
    body = b"%PDF-1.7\nmalformed synthetic document"
    (source_cache / "source.pdf").write_bytes(body)
    with pytest.raises(SourceExtractionError, match="PDF_INVALID"):
        prepare_source(spec_for(body), use_cache=True)


def test_encrypted_pdf_is_not_reclassified_as_a_scan(source_cache):
    body = make_pdf([""], encrypted=True)
    (source_cache / "source.pdf").write_bytes(body)
    with pytest.raises(SourceExtractionError, match="PDF_ENCRYPTED"):
        prepare_source(spec_for(body), use_cache=True)


@pytest.mark.parametrize("field", ["download_path", "pages_path"])
def test_cache_path_traversal_denied(source_cache, field):
    body = make_pdf([""])
    (source_cache / "source.pdf").write_bytes(body)
    with pytest.raises(TrustError, match="SOURCE_CACHE_PATH_FORBIDDEN"):
        prepare_source(spec_for(body, **{field: "../outside.pdf"}), use_cache=True)


def test_cache_symlink_escape_denied(source_cache, tmp_path):
    body = make_pdf([""])
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(body)
    (source_cache / "escape.pdf").symlink_to(outside)
    with pytest.raises(TrustError, match="SOURCE_CACHE_PATH_FORBIDDEN"):
        prepare_source(
            spec_for(body, download_path="data/knowledge_sources/escape.pdf"), use_cache=True
        )


def test_altered_raw_scan_fails_pin_before_fallback(source_cache):
    body = make_pdf([""])
    (source_cache / "source.pdf").write_bytes(body + b"\n% altered")
    with pytest.raises(TrustError, match="SOURCE_HASH_MISMATCH"):
        prepare_source(spec_for(body), use_cache=True)


def write_sidecar(cache, body, *, mutation=None):
    extracted = extract_pdf(body)
    pages = [{"number": page.number, "text": page.text} for page in extracted.pages]
    sidecar = {
        "source_sha256": digest(body),
        "pages": pages,
        "extraction_method": "curator_pdf_text",
        "extraction_review_required": True,
    }
    if mutation:
        mutation(sidecar)
    (cache / "sidecar.json").write_text(json.dumps(sidecar))
    return spec_for(
        body,
        pages_path="data/knowledge_sources/sidecar.json",
        expected_phrases=["Synthetic official text"],
        extraction_sha256=pages_hash(extracted.pages),
    )


def test_pinned_sidecar_keeps_review_required(source_cache):
    body = make_pdf(["Synthetic official text"])
    (source_cache / "source.pdf").write_bytes(body)
    spec = write_sidecar(source_cache, body)
    result = prepare_source(spec, use_cache=True)
    assert result.extraction_method == "curator_pdf_text"
    assert result.extraction_review_required is True
    assert result.extraction_sha256 == spec.extraction_sha256


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda sidecar: sidecar.update(source_sha256="0" * 64), "EXTRACTION_SOURCE_MISMATCH"),
        (
            lambda sidecar: sidecar["pages"][0].update(text="Mutated sidecar text"),
            "EXTRACTION_HASH_MISMATCH",
        ),
        (
            lambda sidecar: sidecar["pages"].append({"number": 2, "text": "Extra page"}),
            "EXTRACTION_PAGE_MISMATCH",
        ),
    ],
)
def test_mutated_sidecar_cannot_be_trusted(source_cache, mutation, code):
    body = make_pdf(["Synthetic official text"])
    (source_cache / "source.pdf").write_bytes(body)
    spec = write_sidecar(source_cache, body, mutation=mutation)
    with pytest.raises(TrustError, match=code):
        prepare_source(spec, use_cache=True)


@pytest.mark.parametrize(
    "url",
    [
        "https://@ojk.go.id/source.pdf",
        "https://user@ojk.go.id/source.pdf",
        "https://user:secret@ojk.go.id/source.pdf",
        "https://ojk.go.id:444/source.pdf",
        "https://ojk.go.id./source.pdf",
        "https://ojk.go.id/\nsource.pdf",
        "https://ojk.go.id\\@example.test/source.pdf",
        "http://ojk.go.id/source.pdf",
    ],
)
def test_credentials_and_unsafe_source_urls_denied_before_fetch(source_cache, monkeypatch, url):
    body = make_pdf([""])
    calls = []
    monkeypatch.setattr(preparation, "fetch_pdf", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(TrustError, match="SOURCE_URL_FORBIDDEN"):
        prepare_source(spec_for(body, source_url=url))
    assert calls == []


def test_scanned_archive_has_only_quarantined_provisions(
    source_cache, database_url, trust_actor, trust_now
):
    from app.core.clock import FrozenClock
    from app.db.connection import connect, create_pool
    from app.modules.knowledge_sources.service import SourceService

    body = make_pdf([""])
    (source_cache / "source.pdf").write_bytes(body)
    prepared = prepare_source(spec_for(body), use_cache=True)
    database = create_pool(database_url)
    database.open(wait=True)
    try:
        result = SourceService(None, database, FrozenClock(trust_now)).stage(
            trust_actor(), prepared
        )
    finally:
        database.close()
    assert result["draft"] == 0
    assert result["quarantined"] == 1
    with connect(database_url) as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM trust.knowledge_documents").fetchone()["n"] == 0
        )
        archived = conn.execute("SELECT payload FROM trust.source_artifacts").fetchone()
        assert bytes(archived["payload"]) == body
        provision = conn.execute(
            "SELECT stage_status,reason_code FROM trust.source_provisions"
        ).fetchone()
        assert provision == {"stage_status": "quarantined", "reason_code": "SCAN_REQUIRES_REVIEW"}
