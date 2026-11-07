from dataclasses import dataclass


@dataclass(frozen=True)
class Caller:
    principal_id: str = "local-operator"
    actor_kind: str = "operator"
    account_id: str | None = None
    run_id: str | None = None
    execution_attempt_id: str | None = None
    fence: int | None = None
