"""Portfolio chat admission is separate from account collection task admission."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.modules.runs.schemas import RuntimeMode, WriteModel


class ManagerChatRequest(WriteModel):
    instruction: str = Field(min_length=1, max_length=2000)
    runtime_mode: RuntimeMode = "simulated"
    idempotency_key: UUID
    manager_session_id: UUID | None = None
    context_account_id: str | None = Field(default=None, min_length=1, max_length=100)
    task_type: Literal["manager_chat"] = "manager_chat"
