"""Explicit request and response contracts for the synthetic trust API."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.knowledge.schemas import (  # noqa: F401
    ArtifactResult,
    CriticalContext,
    DependencyEdge,
    DependencyGraph,
    Document,
    DocumentSummary,
    IngestResult,
    Note,
    QAResult,
    RetirementResult,
    Retrieval,
    RetrievalHit,
    SearchResult,
    Template,
    TemplateResult,
    TemplateSummary,
    VerificationProfile,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(StrictModel):
    role: Literal[
        "knowledge",
        "qa",
        "personal",
        "process",
        "manager",
        "operator",
        "officer",
        "compliance",
        "admin",
    ]
    principal: Literal["cf", "ldn"] = "cf"


class CaseInput(StrictModel):
    is_synthetic: Literal[True]
    timezone: str | None = "Asia/Jakarta"
    borrower_ref: UUID | None = None


class Attempt(StrictModel):
    template_id: UUID


class Conversation(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
    is_synthetic: Literal[True]


class ActionProblem(StrictModel):
    type: Literal["about:blank"]
    title: Literal["Action refused"]
    status: int
    code: str


class CodeProblem(StrictModel):
    code: str


class ValidationIssue(StrictModel):
    location: list[str | int]
    type: str


class ValidationProblem(StrictModel):
    code: Literal["INVALID_REQUEST"]
    errors: list[ValidationIssue]


class TrustHealth(StrictModel):
    status: Literal["ok"]
    simulation_mode: Literal[True]


class TrustReadiness(StrictModel):
    ready: bool
    simulation_mode: Literal[True]
    classifier_profile: str
    semantic_embeddings: Literal[False]


class DemoToken(StrictModel):
    access_token: str
    token_type: Literal["bearer"]
    simulation_mode: Literal[True]
    warning: str


class CaseResult(StrictModel):
    id: UUID
    borrower_ref: UUID
    is_synthetic: Literal[True]


class OutreachResult(StrictModel):
    attempt_id: UUID
    status: Literal["DELIVERED", "NOT_SENT", "BLOCKED", "APPROVED"]
    is_simulated: Literal[True]
    dispatch_id: UUID | None = None
    idempotent_replay: bool | None = None
    reason_codes: list[str] | None = None
    decision: dict[str, Any] | None = None
    message: str | None = None


class ConversationResult(StrictModel):
    id: UUID
    categories: list[str]
    case_paused: bool
    is_synthetic: Literal[True]


class ConversationItem(StrictModel):
    id: UUID
    body: str
    context_redacted: str
    categories: list[str]
    created_at: datetime


class AuditEvent(StrictModel):
    seq: int
    event_id: UUID
    occurred_at: datetime
    actor_id: str
    actor_role: str
    principal: str
    product: str
    event_type: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


class AuditVerification(StrictModel):
    valid: bool
    checked: int
    first_broken_seq: int | None
    head_hash: str | None = None
