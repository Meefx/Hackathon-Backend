"""Extraction tests construct synthetic PDFs without network or external files."""

from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.integrations.knowledge_sources.extract import (
    ExtractedPage,
    SourceExtractionError,
    extract_pdf,
    extract_provision,
)


def make_pdf(texts, *, encrypted=False):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): writer._add_object(font)}
                    )
                }
            )
            stream = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines = escaped.split("\n")
            commands = (
                "BT /F1 12 Tf 50 750 Td "
                + " ".join(f"({line}) Tj 0 -14 Td" for line in lines)
                + " ET"
            )
            stream.set_data(commands.encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-only-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_extract_typed_numbered_pages_and_stable_hash():
    body = make_pdf(["Pasal 1\nSynthetic first article.", "Pasal 2\nSynthetic second article."])
    result = extract_pdf(body)
    assert [page.number for page in result.pages] == [1, 2]
    assert "Synthetic first article." in result.pages[0].text
    assert result.sha256 == extract_pdf(body).sha256
    assert len(result.sha256) == 64
    assert result.empty_pages == ()


def test_mixed_blank_pages_retained_and_marked_for_review():
    result = extract_pdf(make_pdf(["", "Pasal 1\nSynthetic provision."]))
    assert result.empty_pages == (1,)
    assert result.pages[0].text == ""
    assert result.pages[1].number == 2


@pytest.mark.parametrize(
    "body,code",
    [
        (b"", "PDF_INVALID"),
        (b"<html>blocked</html>", "PDF_INVALID"),
        (b"%PDF-1.7\ninvalid", "PDF_INVALID"),
    ],
)
def test_non_pdf_and_corrupt_pdf_fail_closed(body, code):
    with pytest.raises(SourceExtractionError) as caught:
        extract_pdf(body)
    assert caught.value.code == code


def test_encrypted_and_image_only_documents_require_review():
    with pytest.raises(SourceExtractionError) as caught:
        extract_pdf(make_pdf(["Synthetic text."], encrypted=True))
    assert caught.value.code == "PDF_ENCRYPTED"
    with pytest.raises(SourceExtractionError) as caught:
        extract_pdf(make_pdf(["", ""]))
    assert caught.value.code == "SCAN_REQUIRES_REVIEW"


def test_page_and_text_limits_enforced():
    with pytest.raises(SourceExtractionError) as caught:
        extract_pdf(make_pdf(["Synthetic first.", "Synthetic second."]), max_pages=1)
    assert caught.value.code == "PDF_PAGE_LIMIT"
    with pytest.raises(SourceExtractionError) as caught:
        extract_pdf(make_pdf(["Synthetic text beyond limit."]), max_chars=5)
    assert caught.value.code == "PDF_TEXT_LIMIT"


def test_body_and_explanation_with_repeated_article_headers_are_distinct():
    pages = (
        ExtractedPage(1, "Pasal 61\nPrevious article.\nPasal 62\n(1) Synthetic binding rule."),
        ExtractedPage(2, "(2) Continuation retained.\nPasal 63\nNext article."),
        ExtractedPage(
            3,
            "PENJELASAN\nATAS\nSYNTHETIC REGULATION\nII. PASAL DEMI PASAL\nPasal 62\nSynthetic explanation.\nPasal 63\nNext explanation.",
        ),
    )
    body = extract_provision(pages, 1, 2, article=62, section="body")
    assert body.text.startswith("Pasal 62")
    assert "(1) Synthetic binding rule." in body.text
    assert "(2) Continuation retained." in body.text
    assert "Pasal 63" not in body.text
    assert body.section == "body"
    assert (body.page_start, body.page_end) == (1, 2)
    explanation = extract_provision(pages, 3, 3, article=62, section="explanation")
    assert explanation.text == "Pasal 62\nSynthetic explanation."
    assert explanation.sha256 != body.sha256
    with pytest.raises(SourceExtractionError):
        extract_provision(pages, 3, 3, article=62, section="body")
    with pytest.raises(SourceExtractionError):
        extract_provision(pages, 1, 2, article=62, section="explanation")


def test_exact_page_window_prevents_wrong_repeated_header_selection():
    pages = (
        ExtractedPage(1, "Pasal 62\nFirst version."),
        ExtractedPage(2, "Pasal 62\nSecond version."),
    )
    assert extract_provision(pages, 2, 2, article=62).text == "Pasal 62\nSecond version."
    with pytest.raises(SourceExtractionError) as caught:
        extract_provision(pages, 1, 2, article=62)
    assert caught.value.code == "PROVISION_AMBIGUOUS"


def test_suffix_article_and_in_text_reference_are_not_target_headers():
    pages = (
        ExtractedPage(
            1,
            "See Pasal 62 for background.\nPasal 62A\nDifferent article.\nPasal 62\nTarget exact text.\nPasal 63\nExcluded.",
        ),
    )
    result = extract_provision(pages, 1, 1, article=62)
    assert result.text == "Pasal 62\nTarget exact text."


def test_extract_whole_curated_window_without_rewriting():
    pages = (ExtractedPage(1, "Official synthetic wording.\n  Indentation retained."),)
    result = extract_provision(pages, 1, 1)
    assert result.text == pages[0].text
    assert result.article is None
    assert len(result.sha256) == 64


@pytest.mark.parametrize(
    "start,end,article,section",
    [
        (0, 1, 1, "body"),
        (2, 1, 1, "body"),
        (1, 3, 1, "body"),
        (1, 1, 999, "body"),
        (1, 1, True, "body"),
        (1, 1, 1, "unknown"),
    ],
)
def test_invalid_or_missing_provision_denied(start, end, article, section):
    with pytest.raises(SourceExtractionError):
        extract_provision(
            (ExtractedPage(1, "Pasal 1\nSynthetic wording."),),
            start,
            end,
            article=article,
            section=section,
        )


def test_gap_and_empty_extracted_page_cannot_be_used_as_evidence():
    with pytest.raises(SourceExtractionError):
        extract_provision((ExtractedPage(1, "First."), ExtractedPage(3, "Third.")), 1, 3)
    with pytest.raises(SourceExtractionError) as caught:
        extract_provision((ExtractedPage(1, ""),), 1, 1)
    assert caught.value.code == "SCAN_REQUIRES_REVIEW"
