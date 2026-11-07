"""Reviewed corpus metadata must remain useful without a local PDF cache or network.

Raw legal text is deliberately not copied into test fixtures. Its independently
reviewed PDF and extraction pins live in the manifests; cache verification is an
ingestion check. These tests protect applicability, temporal scope and references.
"""

import json
import re
from datetime import date
from pathlib import Path

import pytest

from app.modules.knowledge_sources.preparation import validate_url
from app.modules.knowledge_sources.schemas import SourceSpec

ROOT = Path(__file__).resolve().parents[1]
REVIEW_DATE = date(2026, 11, 7)
CF = ("cf", "spaylater")
LDN = ("ldn", "spinjam")


@pytest.fixture(scope="module")
def corpus():
    entries = [
        SourceSpec.model_validate(entry)
        for path in sorted((ROOT / "app" / "policies" / "corpus").glob("*.json"))
        for entry in json.loads(path.read_text())
    ]
    assert entries, "A fresh checkout must include the curated source manifests"
    assert len({entry.key for entry in entries}) == len(entries)
    return {entry.key: entry for entry in entries}


def scopes(source):
    return {(scope.principal, scope.product) for scope in source.scopes}


def provisions(source):
    return {provision.key: provision for provision in source.provisions}


def test_source_and_extraction_pins_do_not_assert_approval(corpus):
    for source in corpus.values():
        validate_url(source.source_url)
        for value in (source.sha256, source.extraction_sha256):
            assert value is not None and re.fullmatch(r"[a-f0-9]{64}", value), source.key
            assert len(set(value)) > 1, source.key
        assert source.sha256 != source.extraction_sha256, source.key
        metadata = source.model_dump()
        assert metadata.get("approval_status") in (None, "draft"), source.key
        for field in ("approved_by", "approved_at", "qa_by", "approval_token"):
            assert metadata.get(field) is None, (source.key, field)
        assert source.expected_phrases, source.key
        assert source.status_notes, source.key
        assert len(provisions(source)) == len(source.provisions), source.key
        for value in (source.download_path, source.pages_path):
            path = Path(value or "")
            assert path.parts[:2] == ("data", "knowledge_sources"), source.key
            assert ".." not in path.parts and not path.is_absolute(), source.key


def test_context_references_resolve_to_stable_provision_keys_in_compatible_scopes(corpus):
    # Manifest context uses stable source:provision keys, never runtime database UUIDs.
    for source in corpus.values():
        for provision in source.provisions:
            for reference in provision.context_refs:
                parts = reference.split(":")
                assert len(parts) == 2, (source.key, provision.key, reference)
                source_key, provision_key = parts
                assert source_key in corpus, reference
                assert provision_key in provisions(corpus[source_key]), reference
                assert scopes(source) <= scopes(corpus[source_key]), reference


@pytest.mark.parametrize(
    "key,expected",
    [
        ("pojk32_2025", {CF}),
        ("pojk40_2024", {LDN}),
        ("seojk19_2025", {LDN}),
        ("pojk8_2026", {LDN}),
        ("ldn_complaints", {LDN}),
        ("pojk22_2023", {CF, LDN}),
        ("uu_27_2022", {CF, LDN}),
    ],
)
def test_lender_type_prevents_applying_lpbbti_rates_to_bnpl(corpus, key, expected):
    assert scopes(corpus[key]) == expected


@pytest.mark.parametrize(
    "key,effective",
    [
        ("pojk22_2023", "2023-12-22"),
        ("pojk32_2025", "2025-12-15"),
        ("pojk40_2024", "2024-12-27"),
        ("seojk19_2025", "2025-07-31"),
        ("pojk38_2025", "2025-12-22"),
        ("pojk39_2025", "2025-12-22"),
        ("pojk8_2026", "2026-07-06"),
        ("mk_151_2024", "2025-07-30"),
    ],
)
def test_reviewed_effective_dates_use_primary_sources(corpus, key, effective):
    assert corpus[key].effective_from == effective
    assert date.fromisoformat(effective) <= REVIEW_DATE


def test_partial_repeals_do_not_retire_unaffected_collection_articles(corpus):
    assert corpus["pojk22_2023"].legal_status == "partially_repealed"
    assert corpus["pojk40_2024"].legal_status == "partially_repealed"
    for source_key, target_key, locator in (
        ("pojk38_2025", "pojk22_2023", "Pasal 98 ayat (1) huruf b; Pasal 99; Pasal 100"),
        ("pojk39_2025", "pojk22_2023", "Pasal 115 ayat (9)"),
        ("pojk8_2026", "pojk40_2024", "Pasal 187"),
    ):
        assert any(
            relationship["relation"] == "repeals"
            and relationship["target_key"] == target_key
            and relationship["target_locator"] == locator
            for relationship in corpus[source_key].relationships
        )
    assert provisions(corpus["pojk22_2023"])["art_62"].legal_status == "in_force"
    assert provisions(corpus["pojk40_2024"])["art_148"].legal_status == "in_force"
    for obsolete in ("pojk10_2022", "seojk19_2023"):
        if obsolete in corpus:
            assert corpus[obsolete].legal_status == "repealed"
        assert any(
            relationship["relation"] == "repeals" and relationship["target_key"] == obsolete
            for source in corpus.values()
            for relationship in source.relationships
        )


def test_collection_rule_context_keeps_explanation_and_transition(corpus):
    collection = provisions(corpus["pojk22_2023"])
    assert collection["art_62"].article == 62
    assert collection["art_62"].section == "body"
    assert "pojk22_2023:art_62_explanation" in collection["art_62"].context_refs
    assert collection["art_62_explanation"].section == "explanation"
    assert collection["art_62_explanation"].page_end >= 130
    economic = provisions(corpus["seojk19_2025"])["section_xiv"]
    assert economic.start_marker == "XIV. BATAS MAKSIMUM MANFAAT EKONOMI"
    assert economic.end_marker == "XV. FASILITAS MITIGASI RISIKO"
    assert "seojk19_2025:section_xxi" in economic.context_refs
    assert economic.page_start <= 18 and economic.page_end >= 21


def test_unknown_guidance_dates_do_not_become_current_law(corpus):
    for key in ("pojk22_faq", "ldn_complaints"):
        source = corpus[key]
        assert source.source_class == "guidance"
        assert source.effective_from is None
        assert source.legal_status == "review_required"
        assert all(p.legal_status == "review_required" for p in source.provisions)


def test_pdp_purpose_article_stops_before_malformed_next_heading(corpus):
    article = provisions(corpus["uu_27_2022"])["art_28"]
    assert article.article == 28
    # The original PDF text layer puts a stray dot before the Article 29 heading.
    # An explicit boundary prevents Article 29(1) leaking into the purpose rule.
    assert article.end_marker == ". Pasal 29"
    assert article.extraction_review_required is True


def test_court_context_points_to_holdings_and_flags_original_article_53(corpus):
    article = provisions(corpus["uu_27_2022"])["art_53"]
    assert article.legal_status == "review_required"
    assert "mk_151_2024:operative_holding" in article.context_refs
    for key in ("mk_151_2024", "mk_102_2026", "mk_284_2025"):
        holding = provisions(corpus[key])["operative_holding"]
        assert holding.article is None
        assert holding.start_marker == "5. AMAR PUTUSAN"
        assert holding.end_marker == "Demikian diputus"


@pytest.mark.parametrize(
    "key,provision_key,start,end,first,last",
    [
        (
            "mk_151_2024",
            "officer_criteria_reasoning",
            "[3.13.2]",
            "[3.14]",
            155,
            158,
        ),
        (
            "mk_284_2025",
            "court_conclusion",
            "[3.11]",
            "Berdasarkan Undang-Undang Dasar",
            42,
            42,
        ),
    ],
)
def test_court_reasoning_selections_do_not_start_or_end_mid_sentence(
    corpus, key, provision_key, start, end, first, last
):
    # These boundaries were checked against rendered official PDF pages.
    provision = provisions(corpus[key])[provision_key]
    assert provision.start_marker == start
    assert provision.end_marker == end
    assert (provision.page_start, provision.page_end) == (first, last)


def test_2027_decree_effective_date_is_separate_from_future_calendar_coverage(corpus):
    source = corpus["skb_2027"]
    # The decree already exists; its holiday dates belong to next year's calendar.
    assert source.legal_status == "in_force"
    assert source.effective_from == "2026-09-15"
    coverage = source.model_dump()["calendar_coverage"]
    assert coverage == {"from": "2027-01-01", "until": "2028-01-01"}
    assert date.fromisoformat(coverage["from"]) > REVIEW_DATE
    assert source.extraction_review_required is True
    assert source.extraction_method == "ocr"
    for key in ("national_holidays", "collective_leave"):
        assert "2027" in provisions(source)[key].start_marker
        assert provisions(source)[key].extraction_review_required is True
