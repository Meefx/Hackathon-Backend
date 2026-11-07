from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AgentDefinition(BaseModel):
    id: str
    role: Literal["manager", "collection", "knowledge", "qa", "improvement", "copilot"]
    name: str
    description: str
    configured: bool
    available: bool
    runtime_mode: Literal["simulated", "hermes", "unavailable"]
    capabilities: list[str]
    unavailable_reason: str | None
    checked_at: datetime | None


class AgentRegistry(BaseModel):
    items: list[AgentDefinition]


class IntegrationStatus(BaseModel):
    id: str
    kind: Literal["runtime", "knowledge", "mcp", "guardrail", "channel"]
    implemented: bool
    configured: bool
    health: Literal["healthy", "unavailable", "unknown"]
    execution_mode: Literal["simulated", "hermes", "in_process", "sandbox", "unavailable"]
    capabilities: list[str]
    disabled_reason: str | None
    checked_at: datetime | None


class IntegrationRegistry(BaseModel):
    items: list[IntegrationStatus]
