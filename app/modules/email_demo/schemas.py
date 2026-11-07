from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.modules.runs.schemas import RuntimeMode, WriteModel


class EmailDemoRequest(WriteModel):
    idempotency_key: UUID
    runtime_mode: RuntimeMode = "simulated"


class EmailDemo(BaseModel):
    id: str
    account_id: str
    run_id: str
    status: Literal["queued", "running", "submitted", "blocked", "failed", "delivery_unknown"]
    reason_codes: list[str]
    recipient_masked: str
    message_id: str | None
    provider_message_id: str | None
    created_at: datetime
    updated_at: datetime
    runtime_mode: RuntimeMode = "simulated"


class EmailDemoStatus(BaseModel):
    configured: bool
    recipient_masked: str | None
    latest: EmailDemo | None
