from datetime import datetime

from pydantic import BaseModel


class AuditEvent(BaseModel):
    id: str
    account_id: str
    run_id: str | None
    action: str
    actor_kind: str
    outcome: str
    reason_codes: list[str]
    correlation_id: str
    created_at: datetime
    previous_hash: str | None
    event_hash: str
