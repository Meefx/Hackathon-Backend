from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.runs.schemas import Scenario, WriteModel


class ReceiveMessageRequest(WriteModel):
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: UUID
    demo_scenario: Scenario | None = None


class DraftCandidate(WriteModel):
    run_id: str
    account_id: str
    execution_attempt_id: str
    template_id: str | None = None
    text_with_placeholders: str = Field(min_length=1, max_length=4000)
    citation_ids: list[str] = Field(min_length=1, max_length=20)
    idempotency_key: UUID


class Message(BaseModel):
    id: str
    account_id: str
    run_id: str | None = None
    channel: Literal["simulated_chat", "whatsapp", "email", "phone"]
    direction: Literal["inbound", "outbound"]
    text: str
    status: Literal["received", "draft", "simulated_sent", "blocked"]
    version: int
    citation_ids: list[str]
    created_at: datetime


class DispatchRequest(WriteModel):
    idempotency_key: UUID
    expected_message_version: int = Field(ge=1)


class DispatchResult(BaseModel):
    message_id: str
    status: Literal["simulated_sent", "blocked", "unavailable"]
    reason_codes: list[str]
    audit_event_id: str
