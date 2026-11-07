"""Request and response contracts for the native knowledge producer."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Document(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=64000)
    source_uri: str = Field(max_length=2000)
    source_class: Literal["synthetic", "primary", "guidance"] = "synthetic"
    source_version: str = Field(min_length=1, max_length=100)
    source_locator: str = Field(min_length=1, max_length=300)
    review_status: Literal["synthetic", "verified", "review_required"] = "synthetic"
    effective_from: datetime
    effective_until: datetime | None = None
    criticality: Literal["standard", "critical"] = "standard"
    access_roles: list[str] = Field(min_length=1, max_length=10)


class Template(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=400)
    document_ids: list[UUID] = Field(min_length=1, max_length=20)
    evidence_weights: dict[str, float] = Field(default_factory=dict)


class Note(StrictModel):
    note: str = Field(min_length=3, max_length=1000)


class Retrieval(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class ArtifactResult(StrictModel):
    id: UUID
    status: Literal["draft", "approved", "retired", "suspended"]
    content_hash: str
    hash: str


class IngestResult(ArtifactResult):
    chunk_count: int
    embedding_profile: str


class QAResult(ArtifactResult):
    qa_passed: Literal[True]


class RetirementResult(ArtifactResult):
    suspended_template_ids: list[UUID]


class TemplateResult(ArtifactResult):
    document_ids: list[UUID]


class DocumentSummary(StrictModel):
    id: UUID
    title: str
    status: Literal["draft", "approved", "retired"]
    source_class: Literal["synthetic", "primary", "guidance"]
    source_version: str
    review_status: Literal["synthetic", "verified", "review_required"]
    criticality: Literal["standard", "critical"]
    effective_from: datetime
    effective_until: datetime | None
    content_hash: str


class TemplateSummary(StrictModel):
    id: UUID
    title: str
    status: Literal["draft", "approved", "suspended"]
    content_hash: str


class RetrievalHit(StrictModel):
    id: UUID
    chunk_id: UUID
    document_id: UUID
    locator: str
    parent_locator: str | None
    text: str
    title: str
    source_uri: str
    source_version: str
    source_locator: str
    source_class: str
    review_status: str
    criticality: str
    document_hash: str
    snippet_hash: str
    approved_at: datetime
    effective_from: datetime
    effective_until: datetime | None
    score: float


class CriticalContext(StrictModel):
    document_version: str
    content_hash: str
    chunk_id: UUID
    document_id: UUID
    locator: str
    parent_locator: str | None
    text: str


class VerificationProfile(StrictModel):
    embedding_profile: str
    semantic_embeddings: Literal[False]
    source_correctness_verified_by_code: Literal[False]
    review_depth: Literal["critical", "standard"]
    retrieved_text_is_untrusted: Literal[True]
    critical_integrity_checks: bool
    context_expansion: Literal["full_source_within_budget", "none"]


class SearchResult(StrictModel):
    results: list[RetrievalHit]
    critical_context: list[CriticalContext]
    verification_profile: VerificationProfile


class DependencyEdge(StrictModel):
    document_id: UUID
    template_id: UUID
    relation: str
    weight: float
    document_status: str
    template_status: str


class DependencyGraph(StrictModel):
    edges: list[DependencyEdge]
    weight_semantics: str
