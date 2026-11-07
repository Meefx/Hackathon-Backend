from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class AutomationCounts(BaseModel):
    queued: int = 0
    running: int = 0
    sent: int = 0
    deferred: int = 0
    needs_attention: int = 0


class AutomationAttentionItem(BaseModel):
    id: str
    account_id: str
    run_id: str | None
    reason_codes: list[str]
    updated_at: datetime


class AutomationStatus(BaseModel):
    enabled: bool
    daily_start_time: str
    timezone: str
    runtime_mode: Literal["simulated", "hermes"]
    business_date: date
    last_scan_at: datetime | None
    counts: AutomationCounts
    attention_items: list[AutomationAttentionItem] = Field(default_factory=list, max_length=5)
