"""Explicit public-document adapters; no database or model authority."""

from app.integrations.knowledge_sources.extract import (
    ExtractedPage,
    ExtractionResult,
    ProvisionResult,
    SourceExtractionError,
    extract_pdf,
    extract_provision,
)
from app.integrations.knowledge_sources.fetch import FetchResult, SourceFetchError, fetch_pdf

__all__ = [
    "ExtractedPage",
    "ExtractionResult",
    "FetchResult",
    "ProvisionResult",
    "SourceExtractionError",
    "SourceFetchError",
    "extract_pdf",
    "extract_provision",
    "fetch_pdf",
]
