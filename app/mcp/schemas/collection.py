"""MCP assignment locators never confer authority; the service verifies each one."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.conversations.schemas import DraftCandidate, Message
from app.modules.knowledge_context.contracts import KnowledgeResult
from app.modules.runs.schemas import ArtifactCandidate


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=100)
    run_id: str = Field(min_length=1, max_length=100)
    execution_attempt_id: str = Field(min_length=1, max_length=100)
    fence: int = Field(ge=1)


class HistoryRequest(Assignment):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class HistoryPage(BaseModel):
    items: list[Message] = Field(max_length=100)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ContextRequest(Assignment):
    query: str = Field(min_length=1, max_length=1000)
    purpose: Literal["collection_draft", "qa_check", "copilot", "improvement_review"]
    locale: Literal["id-ID"] = "id-ID"
    top_k: int = Field(default=3, ge=1, le=5)
    deadline_at: datetime


class KnowledgeSnapshot(KnowledgeResult):
    id: str
    run_id: str
    required: bool
    retrieved_at: datetime
    snapshot_hash: str


class DraftRequest(Assignment):
    candidate: DraftCandidate

    @model_validator(mode="after")
    def bind_candidate(self):
        if any(
            getattr(self.candidate, field) != getattr(self, field)
            for field in ("run_id", "account_id", "execution_attempt_id")
        ):
            raise ValueError("Candidate must match assignment")
        return self


class ArtifactRequest(Assignment):
    candidate: ArtifactCandidate

    @model_validator(mode="after")
    def bind_candidate(self):
        if any(
            getattr(self.candidate, field) != getattr(self, field)
            for field in ("run_id", "account_id", "execution_attempt_id")
        ):
            raise ValueError("Artifact must match assignment")
        return self


class ReviewRequest(Assignment):
    reason: Literal["hardship", "dispute", "human_request"]
    note: str = Field(min_length=1, max_length=1000)
    idempotency_key: UUID
