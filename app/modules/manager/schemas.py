from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ManagerSession(BaseModel):
    id: str
    account_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    last_run_id: str | None


class ManagerTurn(BaseModel):
    id: str
    session_id: str
    turn_index: int
    run_id: str | None
    role: Literal["operator", "manager"]
    content: str
    created_at: datetime
