"""Transport-neutral runtime contract. Business completion remains a domain decision."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

RuntimeTask = Literal["follow_up", "collector_brief", "improvement_review", "manager_chat"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HistoryMessage(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class RuntimeRequest(StrictModel):
    application_run_id: str = Field(min_length=1, max_length=100)
    account_id: str | None = Field(default=None, min_length=1, max_length=100)
    context_account_id: str | None = Field(default=None, min_length=1, max_length=100)
    execution_attempt_id: str = Field(min_length=1, max_length=100)
    fence: int = Field(ge=1)
    task_type: RuntimeTask
    instruction: str = Field(min_length=1, max_length=2000)
    history: list[HistoryMessage] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def assignment_matches_task(self):
        if self.task_type == "manager_chat":
            if self.account_id is not None:
                raise ValueError("manager_chat requires portfolio scope")
        elif not self.account_id:
            raise ValueError("Account tasks require account_id")
        return self


class FinalRuntimeResult(StrictModel):
    application_run_id: str = Field(min_length=1, max_length=100)
    task_type: RuntimeTask
    summary: str = Field(min_length=1, max_length=4000)
    artifact_ids: list[str] = Field(max_length=20)
    candidate_message_id: str | None = None
    citation_ids: list[str] = Field(max_length=20)
    review_requested: bool


class RuntimeProbe(StrictModel):
    available: bool
    actual_runtime_mode: Literal["hermes", "simulated", "unavailable"]
    capabilities: list[str] = Field(default_factory=list)
    reason_code: str | None = None


RuntimeState = Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]


class RuntimeSubmission(StrictModel):
    external_run_id: str
    actual_runtime_mode: Literal["hermes", "simulated"]
    status: RuntimeState


class RuntimeStatus(StrictModel):
    external_run_id: str
    actual_runtime_mode: Literal["hermes", "simulated"]
    status: RuntimeState
    final_result: FinalRuntimeResult | None = None
    reason_code: str | None = None
    runtime: dict[str, str] = Field(default_factory=dict)
    usage: dict[str, int] = Field(default_factory=dict)


class RuntimeError(Exception):
    """Safe provider error; never includes provider response bodies or credentials."""

    def __init__(self, code: str, *, ambiguous: bool = False):
        self.code = code
        self.ambiguous = ambiguous
        super().__init__(code)


class AgentRuntime(Protocol):
    def probe(self) -> RuntimeProbe: ...
    def submit(self, request: RuntimeRequest, idempotency_key: str) -> RuntimeSubmission: ...
    def get_status(self, external_run_id: str) -> RuntimeStatus: ...
    def request_stop(self, external_run_id: str) -> RuntimeStatus: ...
