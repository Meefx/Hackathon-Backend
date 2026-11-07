"""All new domain code obtains time through this boundary."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    def __init__(self, instant: datetime):
        if instant.tzinfo is None:
            raise ValueError("Clock requires a timezone")
        self.instant = instant

    def now(self) -> datetime:
        return self.instant
