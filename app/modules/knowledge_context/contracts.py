"""Bounded collection evidence, distinct raw producer and canonical snapshot hashes."""

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, Field, model_serializer, model_validator


class KnowledgeQuery(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    purpose: Literal["collection_draft", "qa_check", "copilot", "improvement_review"]
    locale: Literal["id-ID"] = "id-ID"
    top_k: int = Field(default=3, ge=1, le=5, strict=True)
    deadline_at: AwareDatetime


class KnowledgeSource(BaseModel):
    document_id: str
    document_version: str
    snippet_id: str
    citation_id: str
    title: str
    excerpt: str = Field(max_length=4000)
    content_hash: str
    document_hash: str | None = None
    snippet_hash: str | None = None
    approved_at: AwareDatetime
    effective_from: AwareDatetime | None = None
    effective_until: AwareDatetime | None = None
    source_uri: str | None = None


class CriticalContext(BaseModel):
    document_id: str
    document_version: str
    chunk_id: str
    locator: str
    text: str = Field(max_length=24000)
    content_hash: str


class EvidenceContext(BaseModel):
    critical_context: list[CriticalContext] = Field(default_factory=list, max_length=64)
    verification_profile: dict[str, Any] | None = None

    @model_validator(mode="after")
    def context_budget(self):
        if sum(len(chunk.text) for chunk in self.critical_context) > 24000:
            raise ValueError("Critical context exceeds the review budget")
        return self


class KnowledgeResult(EvidenceContext):
    status: Literal["ready", "no_results", "unavailable", "error"]
    source_mode: Literal["knowledge_module", "demo_fixture"]
    sources: list[KnowledgeSource] = Field(default_factory=list, max_length=5)
    reason_code: str | None = None


class ReferenceValidation(BaseModel):
    valid: bool
    checked_at: AwareDatetime
    invalid_citation_ids: list[str] = Field(default_factory=list)
    reference_version: str


class LockedKnowledgeReferences(Protocol):
    def validate(self, at: datetime) -> ReferenceValidation: ...


class KnowledgeGateway(Protocol):
    def query_approved(self, request: KnowledgeQuery, caller) -> KnowledgeResult: ...
    def validate_references(
        self, references: list[KnowledgeSource], at: datetime, caller
    ) -> ReferenceValidation: ...
    def lock_references(self, connection, references, caller) -> LockedKnowledgeReferences: ...


class KnowledgeSnapshot(KnowledgeResult):
    id: str
    run_id: str
    required: bool
    retrieved_at: AwareDatetime
    snapshot_hash: str
    snapshot_hash_version: Literal[1, 2] = 1


    @model_serializer(mode="wrap")
    def serialize_versioned_sources(self, handler):
        payload = handler(self)
        if self.snapshot_hash_version == 1:
            for source in payload.get("sources", []):
                for field in ("document_hash", "snippet_hash"):
                    if source.get(field) is None:
                        source.pop(field, None)
        return payload
