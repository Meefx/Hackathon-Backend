from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ManagerSession(BaseModel):
    id: str
    account_id: str | None
    context_account_id: str | None = None
    title: str
    created_at: datetime
    updated_at: datetime
    last_run_id: str | None
    origin: Literal["operator", "scheduled"] = "operator"


class ManagerTurn(BaseModel):
    id: str
    session_id: str
    turn_index: int
    run_id: str | None
    role: Literal["operator", "manager", "scheduler"]
    content: str
    created_at: datetime


class TaskProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=100)
    task_type: Literal["follow_up", "collector_brief", "improvement_review"]
    instruction: str = Field(min_length=1, max_length=2000)
