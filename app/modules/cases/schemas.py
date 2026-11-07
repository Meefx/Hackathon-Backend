from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.runs.schemas import WriteModel

ContactState = Literal["active", "paused_human_review", "paused_operator"]


class PolicyDecision(BaseModel):
    account_id: str
    case_version: int
    allowed: bool
    reason_codes: list[str]
    policy_version: str | None
    contact_state: ContactState
    evaluated_at: datetime


class CaseActionRequest(WriteModel):
    expected_case_version: int = Field(ge=1)
    idempotency_key: UUID
    note: str = Field(min_length=1, max_length=1000)


class CaseActionResult(BaseModel):
    account_id: str
    case_version: int
    contact_state: ContactState
    action_applied: bool
    reason_codes: list[str]
    review_id: str | None = None
