from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.cases.schemas import CaseActionResult
from app.modules.runs.schemas import WriteModel


class ReviewItem(BaseModel):
    id: str
    account_id: str
    run_id: str | None = None
    reason: Literal["hardship", "dispute", "human_request", "operator_takeover"]
    status: Literal["open", "resolved"]
    version: int
    created_at: datetime
    resolved_at: datetime | None = None


class ReviewResolution(WriteModel):
    decision: Literal["keep_paused", "request_resume"]
    note: str = Field(min_length=1, max_length=1000)
    expected_version: int = Field(ge=1)
    idempotency_key: UUID


class ReviewResolutionResult(BaseModel):
    review: ReviewItem
    case: CaseActionResult
