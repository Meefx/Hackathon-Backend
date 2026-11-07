from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.manager.schemas import TaskProposal

TaskType = Literal["follow_up", "collector_brief", "improvement_review"]
RuntimeMode = Literal["simulated", "hermes"]
RunStatus = Literal[
    "queued", "running", "completed", "blocked", "failed", "cancelled", "interrupted"
]
Scenario = Literal["reminder", "hardship", "dispute", "human_request"]


class WriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartRunRequest(WriteModel):
    instruction: str = Field(min_length=1, max_length=2000)
    task_type: TaskType = "follow_up"
    runtime_mode: RuntimeMode = "simulated"
    idempotency_key: UUID
    manager_session_id: UUID | None = None
    review_id: UUID | None = None
    demo_scenario: Scenario | None = None

    @model_validator(mode="after")
    def scenario_is_simulated(self):
        if self.demo_scenario and self.runtime_mode != "simulated":
            raise ValueError("demo_scenario requires simulated runtime")
        if self.review_id and (self.task_type != "collector_brief" or self.demo_scenario):
            raise ValueError("review_id requires collector_brief without a demo_scenario")
        return self


class RunAccepted(BaseModel):
    run_id: str
    manager_session_id: str
    status: RunStatus
    version: int
    created_at: datetime


class CancelRunRequest(WriteModel):
    expected_version: int = Field(ge=1)
    idempotency_key: UUID


class RecoverRunRequest(CancelRunRequest):
    action: Literal["reconcile", "retry_safe_step"]


class RunStep(BaseModel):
    id: str
    sequence: int
    role: str
    status: str
    summary: str
    evidence_origin: str
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RunArtifact(BaseModel):
    id: str
    run_id: str
    kind: Literal[
        "draft", "qa_report", "collector_brief", "improvement_proposal",
        "manager_response", "manager_task_proposal",
    ]
    text: str
    message_id: str | None = None
    citation_ids: list[str] = []
    review_state: Literal["pending", "approved", "rejected"] | None = None
    created_at: datetime
    proposal: TaskProposal | None = None


class RunSnapshot(BaseModel):
    id: str
    account_id: str | None
    manager_session_id: str
    origin: Literal["operator", "scheduled"] = "operator"
    scope: Literal["account", "portfolio"] = "account"
    context_account_id: str | None = None
    status: RunStatus
    outcome: str | None = None
    requested_runtime_mode: RuntimeMode
    actual_runtime_mode: RuntimeMode | None = None
    phase: str
    steps: list[RunStep]
    artifacts: list[RunArtifact]
    reason_codes: list[str]
    version: int
    session_turn_index: int
    latest_event_seq: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    cancellation_requested_at: datetime | None = None
    allowed_actions: list[str]


class RunEvent(BaseModel):
    sequence: int
    run_id: str
    type: str
    safe_summary: str
    evidence_origin: str
    created_at: datetime


class ArtifactCandidate(WriteModel):
    run_id: str
    account_id: str
    execution_attempt_id: str
    kind: Literal["qa_report", "collector_brief", "improvement_proposal"]
    text: str = Field(min_length=1, max_length=10000)
    citation_ids: list[str] = Field(default_factory=list, max_length=20)
    idempotency_key: UUID


class RunEventPage(BaseModel):
    items: list[RunEvent]
    next_sequence: int
    has_more: bool
