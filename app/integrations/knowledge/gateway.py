import importlib

from app.modules.knowledge_context.contracts import (
    KnowledgeResult,
    KnowledgeSource,
    ReferenceValidation,
)
from app.shared.idempotency import digest

EXCERPT = "Gunakan bahasa sopan, jelaskan fakta tagihan, dan tawarkan bantuan. Kesulitan bayar atau sengketa wajib diteruskan ke petugas manusia tanpa tekanan."


class DemoKnowledgeGateway:
    def query_approved(self, request, caller):
        from datetime import UTC, datetime

        return KnowledgeResult(
            status="ready",
            source_mode="demo_fixture",
            sources=[
                KnowledgeSource(
                    document_id="demo-collection-sop",
                    document_version="1",
                    snippet_id="respectful-contact",
                    citation_id="demo-sop-v1-contact",
                    title="Synthetic Collection SOP",
                    excerpt=EXCERPT,
                    content_hash=digest(EXCERPT),
                    approved_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            ],
        )

    def validate_references(self, references, at, caller):
        invalid = [
            s.citation_id
            for s in references
            if s.document_id != "demo-collection-sop"
            or s.document_version != "1"
            or s.content_hash != digest(EXCERPT)
            or s.excerpt != EXCERPT
        ]
        return ReferenceValidation(
            valid=not invalid,
            checked_at=at,
            invalid_citation_ids=invalid,
            reference_version="demo-v1",
        )


class UnavailableKnowledgeGateway:
    def query_approved(self, request, caller):
        return KnowledgeResult(
            status="unavailable",
            source_mode="knowledge_module",
            reason_code="knowledge_unavailable",
        )

    def validate_references(self, references, at, caller):
        return ReferenceValidation(
            valid=False,
            checked_at=at,
            invalid_citation_ids=[s.citation_id for s in references],
            reference_version="unavailable",
        )


def get_gateway(settings):
    if settings.knowledge_mode == "demo_fixture" and settings.demo_fixture_enabled:
        return DemoKnowledgeGateway()
    if settings.knowledge_mode == "in_process" and settings.knowledge_service_factory:
        module, name = settings.knowledge_service_factory.rsplit(":", 1)
        return getattr(importlib.import_module(module), name)()
    return UnavailableKnowledgeGateway()
