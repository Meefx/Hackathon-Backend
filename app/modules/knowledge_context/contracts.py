from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class KnowledgeQuery(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    purpose: Literal["collection_draft", "qa_check", "copilot", "improvement_review"]
    locale: Literal["id-ID"] = "id-ID"
    top_k: int = Field(default=3, ge=1, le=5)
    deadline_at: datetime


class KnowledgeSource(BaseModel):
    document_id: str
    document_version: str
    snippet_id: str
    citation_id: str
    title: str
    excerpt: str = Field(max_length=4000)
    content_hash: str
    approved_at: datetime
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    source_uri: str | None = None


class KnowledgeResult(BaseModel):
    status: Literal["ready", "no_results", "unavailable", "error"]
    source_mode: Literal["knowledge_module", "demo_fixture"]
    sources: list[KnowledgeSource] = Field(default_factory=list, max_length=5)
    reason_code: str | None = None


class ReferenceValidation(BaseModel):
    valid: bool
    checked_at: datetime
    invalid_citation_ids: list[str] = []
    reference_version: str


class KnowledgeGateway(Protocol):
    def query_approved(self, request: KnowledgeQuery, caller) -> KnowledgeResult: ...
    def validate_references(
        self, references: list[KnowledgeSource], at: datetime, caller
    ) -> ReferenceValidation: ...


class KnowledgeSnapshot(BaseModel):
    id: str
    run_id: str
    status: Literal["ready", "no_results", "unavailable", "error"]
    source_mode: Literal["knowledge_module", "demo_fixture"]
    required: bool
    retrieved_at: datetime
    snapshot_hash: str
    sources: list[KnowledgeSource]
    reason_code: str | None
