"""Bounded portfolio tool contracts for an active manager-chat assignment."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.cases.schemas import PolicyDecision
from app.modules.reviews.schemas import ReviewItem
from app.schemas import Account, CollectionStatus


class PortfolioAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=100)
    execution_attempt_id: str = Field(min_length=1, max_length=100)
    fence: int = Field(ge=1)


class PortfolioSearchRequest(PortfolioAssignment):
    query: str = Field(default="", max_length=100)
    status: CollectionStatus | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class PortfolioAccountRequest(PortfolioAssignment):
    account_id: str = Field(min_length=1, max_length=100)


class ManagerKnowledgeRequest(PortfolioAssignment):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=5, strict=True)


class PortfolioAccountPage(BaseModel):
    items: list[Account] = Field(max_length=100)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class PortfolioMessage(BaseModel):
    direction: str
    text: str
    created_at: datetime


class PortfolioAccountContext(BaseModel):
    account: Account
    policy: PolicyDecision
    recent_messages: list[PortfolioMessage] = Field(max_length=5)
    open_reviews: list[ReviewItem] = Field(max_length=20)


class ManagerResponseRequest(PortfolioAssignment):
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: UUID


class AccountTaskProposalRequest(PortfolioAccountRequest):
    task_type: Literal["follow_up", "collector_brief", "improvement_review"]
    instruction: str = Field(min_length=1, max_length=2000)
    idempotency_key: UUID
