"""Curated source manifests and explicit review/download contracts."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

LegalStatus = Literal[
    "in_force", "partially_repealed", "future", "repealed", "draft", "review_required"
]


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal: Literal["cf", "ldn"]
    product: Literal["spaylater", "spinjam"]

    @model_validator(mode="after")
    def valid_pair(self) -> "Scope":
        if (self.principal, self.product) not in {("cf", "spaylater"), ("ldn", "spinjam")}:
            raise ValueError("Invalid principal/product scope")
        return self


class ProvisionSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    key: str = Field(min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9_.-]+$")
    locator: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=500)
    section: Literal["body", "explanation"] = "body"
    article: int | None = Field(default=None, ge=1, le=1000)
    page_start: int = Field(ge=1, le=500)
    page_end: int = Field(ge=1, le=500)
    legal_status: LegalStatus = "review_required"
    effective_from: str | None = None
    effective_until: str | None = None
    start_marker: str | None = None
    end_marker: str | None = None
    extraction_review_required: bool = False
    context_refs: list[str] = Field(default_factory=list, max_length=30)
    rule_ids: list[str] = Field(default_factory=list, max_length=30)


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    key: str = Field(min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9_.-]+$")
    title: str = Field(min_length=1, max_length=500)
    publisher: str = Field(min_length=1, max_length=300)
    source_class: Literal["primary", "guidance"]
    source_url: str = Field(max_length=2500)
    landing_url: str | None = None
    source_version: str = Field(min_length=1, max_length=100)
    effective_from: str | None = None
    effective_until: str | None = None
    legal_status: LegalStatus = "review_required"
    scopes: list[Scope] = Field(min_length=1, max_length=2)
    status_notes: str | list[str] = ""
    provisions: list[ProvisionSpec] = Field(default_factory=list, max_length=100)
    relationships: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    expected_phrases: list[str] = Field(default_factory=list, max_length=30)
    download_path: str | None = None
    pages_path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    extraction_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    extraction_method: str = "pdf_text"
    extraction_review_required: bool = False


class SourceSummary(BaseModel):
    id: UUID
    source_key: str
    title: str
    publisher: str
    source_url: str
    final_url: str
    source_version: str
    source_class: str
    artifact_sha256: str
    legal_status: LegalStatus
    effective_from: datetime | None
    effective_until: datetime | None
    created_at: datetime
    page_count: int
    byte_size: int
    extraction_method: str
    extraction_review_required: bool
    provision_count: int


class SourcePage(BaseModel):
    items: list[SourceSummary]
    total: int
    limit: int
    offset: int


class ProvisionSummary(BaseModel):
    id: UUID
    provision_key: str
    locator: str
    page_start: int
    page_end: int
    knowledge_document_id: UUID | None
    stage_status: str
    reason_code: str | None
    text_sha256: str
    knowledge_status: str | None
    metadata: dict[str, Any]


class SourceDetail(SourceSummary):
    metadata: dict[str, Any]
    provisions: list[ProvisionSummary]
