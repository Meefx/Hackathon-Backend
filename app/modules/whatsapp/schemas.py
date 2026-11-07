from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.modules.conversations.schemas import Message
from app.modules.runs.schemas import WriteModel


class LinkRequest(WriteModel):
    idempotency_key: UUID


class ConnectionStatus(BaseModel):
    account_id: str
    configured: bool
    linked: bool
    session_status: str
    recipient_masked: str | None = None
    mode: Literal["sandbox"] = "sandbox"
    reason_codes: list[str] = []


class WebhookResult(BaseModel):
    status: Literal["processed", "ignored", "duplicate"]


class OutboundMessageItem(BaseModel):
    borrower_name: str
    loan_reference: str
    message: Message


class OutboundMessagePage(BaseModel):
    items: list[OutboundMessageItem]
    total: int
    limit: int
    offset: int
