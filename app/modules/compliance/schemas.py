from datetime import date
from typing import Literal

from pydantic import BaseModel


class CurrentPolicy(BaseModel):
    id: str
    version: str
    timezone: Literal["Asia/Jakarta"]
    contact_start_hour: int
    contact_end_hour: int
    coverage_until: date
    maximum_daily_messages: int
    mode: str
