from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.config import Settings
from app.db.connection import connect
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.manager.service import reserve_run_session
from app.modules.runs import repository as repo
from app.modules.runs.schemas import StartRunRequest
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.shared.idempotency import record, replay

if TYPE_CHECKING:
    from app.modules.manager.chat_schemas import ManagerChatRequest


def authorize_assignment(
    connection: Connection[dict[str, Any]],
    caller: Caller,
    account_id: str | None,
    run_id: str | None,
    now: datetime,
    allow_inactive: bool = False,
) -> None:
    """Authorize a public run capability without exposing persistence internals."""
    repo.check_assignment(connection, caller, account_id, run_id, now, allow_inactive)


def authorize_portfolio_assignment(
    connection: Connection[dict[str, Any]], run_id: str, caller: Caller, now: datetime
) -> dict[str, Any]:
    """Require an active agent/worker lease for a portfolio manager run."""
    run = repo.require_run(connection, run_id, True)
    if (
        caller.actor_kind not in ("worker", "agent")
        or run["account_id"] is not None
        or run["payload"].get("task_type") != "manager_chat"
    ):
        raise DomainError("assignment_denied", "Active portfolio chat assignment required", 403)
    authorize_assignment(connection, caller, None, run_id, now)
    return run


def record_advisory_event(
    connection: Connection[dict[str, Any]], run_id: str, now: datetime
) -> None:
    """Record a bounded event after a fenced manager artifact write."""
    repo.add_event(connection, run_id, "artifact_submitted", "Manager advisory saved", now)


def start_run(settings, account_id, request, caller, now):
    with connect(settings.database_url) as connection:
        return start_run_in_transaction(connection, settings, account_id, request, caller, now)


def start_run_in_transaction(
    connection: Connection[dict[str, Any]],
    settings: Settings,
    account_id: str | None,
    request: "StartRunRequest | ManagerChatRequest",
    caller: Caller,
    now: datetime,
) -> dict[str, Any]:
    """Admit a durable run inside the caller-owned transaction."""
    payload = request.model_dump(mode="json")
    if getattr(request, "review_id", None) is None:
        payload.pop("review_id", None)
    key = str(request.idempotency_key)
    portfolio = account_id is None
    if portfolio:
        if caller.actor_kind != "operator" or request.task_type != "manager_chat":
            raise DomainError("assignment_denied", "Operator portfolio chat required", 403)
        scope = "start_manager_chat:" + caller.principal_id
    else:
        assert account_id is not None
        if request.task_type == "manager_chat":
            raise DomainError("assignment_denied", "Portfolio chat requires global scope", 403)
        scope = "start_run:" + account_id + ":" + caller.principal_id
        lock_case(connection, account_id)
    if old := replay(connection, scope, key, payload):
        return old
    review_id = getattr(request, "review_id", None)
    if review_id:
        from app.modules.reviews.service import get_account_review

        assert account_id is not None

        if caller.actor_kind != "operator":
            raise DomainError("review_forbidden", "Operator review discussion required", 403)
        get_account_review(connection, account_id, str(review_id))
    session_id = str(request.manager_session_id or uuid4())
    turn = reserve_run_session(
        connection,
        session_id,
        account_id,
        caller.principal_id,
        request.instruction,
        now,
        existing=request.manager_session_id is not None,
        context_account_id=getattr(request, "context_account_id", None),
    )
    run_id = str(uuid4())
    connection.execute(
        "INSERT INTO collection_runs(id,account_id,manager_session_id,caller_id,"
        "request_key,payload,status,requested_runtime_mode,session_turn_index,created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            run_id,
            account_id,
            session_id,
            caller.principal_id,
            key,
            Jsonb(payload),
            "queued",
            request.runtime_mode,
            turn,
            now,
        ),
    )
    connection.execute("INSERT INTO collection_jobs(run_id) VALUES (%s)", (run_id,))
    connection.execute(
        "INSERT INTO manager_turns VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (
            str(uuid4()),
            session_id,
            turn,
            run_id,
            "scheduler" if caller.actor_kind == "scheduler" else "operator",
            request.instruction,
            now,
        ),
    )
    connection.execute(
        "UPDATE manager_sessions SET last_run_id=%s,updated_at=%s WHERE id=%s",
        (run_id, now, session_id),
    )
    repo.add_event(connection, run_id, "queued", "Task admitted to durable queue", now)
    if not portfolio:
        append_event(
            connection, account_id, "run_submitted", caller.actor_kind, "queued", now, run_id
        )
    result = {
        "run_id": run_id,
        "manager_session_id": session_id,
        "status": "queued",
        "version": repo.require_run(connection, run_id)["version"],
        "created_at": now.isoformat(),
    }
    record(connection, scope, key, payload, result)
    return result


def cancel_run(settings, run_id, request, caller, now):
    with connect(settings.database_url) as connection:
        run = repo.require_run(connection, run_id, True)
        payload = request.model_dump(mode="json")
        scope = "cancel:" + run_id
        if old := replay(connection, scope, str(request.idempotency_key), payload):
            return old
        if run["version"] != request.expected_version:
            raise DomainError("version_conflict", "Run version has changed")
        if run["status"] not in ("queued", "running"):
            raise DomainError("run_terminal", "Run cannot be cancelled")
        connection.execute(
            "UPDATE collection_runs SET cancellation_requested_at=%s,version=version+1 WHERE id=%s",
            (now, run_id),
        )
        if run["status"] == "queued":
            repo.finish(connection, run_id, "cancelled", None, "Cancelled before execution", now)
        else:
            repo.add_event(connection, run_id, "cancellation_requested", "Stop requested", now)
        from app.modules.runs.schemas import RunSnapshot

        result = RunSnapshot.model_validate(repo.snapshot(connection, run_id)).model_dump(
            mode="json"
        )
        record(connection, scope, str(request.idempotency_key), payload, result)
        return result


def recover_run(settings, run_id, request, caller, clock):
    """Read existing provider status; never create a second external admission."""
    from app.integrations.agents.contracts import RuntimeError
    from app.integrations.hermes.runtime import create_hermes_runtime
    from app.modules.runs.schemas import RunSnapshot
    from app.workflows.collection import validate_final

    scope = "recover:" + run_id
    payload = request.model_dump(mode="json")
    key = str(request.idempotency_key)
    with connect(settings.database_url) as connection:
        run = repo.require_run(connection, run_id, True)
        if old := replay(connection, scope, key, payload):
            return old
        if run["version"] != request.expected_version:
            raise DomainError("version_conflict", "Run version has changed")
        if run["status"] != "interrupted" or request.action != "reconcile":
            raise DomainError("recovery_not_allowed", "Recovery action is unavailable")
        effects = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM run_artifacts WHERE run_id=%s) OR "
            "EXISTS(SELECT 1 FROM collection_messages WHERE run_id=%s) OR "
            "EXISTS(SELECT 1 FROM collection_reviews WHERE run_id=%s) AS present",
            (run_id, run_id, run_id),
        ).fetchone()["present"]
        if not run["external_run_id"] and (run["runtime_snapshot"] or effects):
            raise DomainError(
                "reconciliation_required", "External admission or effects remain ambiguous"
            )
    # Provider I/O happens after the transaction closes. Version is checked again before commit.
    status = None
    if run["external_run_id"]:
        try:
            status = create_hermes_runtime(settings).get_status(run["external_run_id"])
        except RuntimeError as error:
            raise DomainError(error.code, "Runtime reconciliation is unavailable", 503) from error
        if status.status in ("queued", "running", "interrupted"):
            raise DomainError(
                "runtime_interrupted", "External execution is not conclusively terminal"
            )
    now = clock()
    with connect(settings.database_url) as connection:
        run = repo.require_run(connection, run_id, True)
        if old := replay(connection, scope, key, payload):
            return old
        if run["version"] != request.expected_version or run["status"] != "interrupted":
            raise DomainError("version_conflict", "Run changed during reconciliation")
        if status:
            if status.status == "completed":
                summary = "Runtime completed with an invalid business result"
                try:
                    if not status.final_result:
                        raise DomainError(
                            "result_invalid", "Completed runtime lacks a final result"
                        )
                    new_status, outcome, reasons = validate_final(
                        connection, run, status.final_result
                    )
                    summary = status.final_result.summary
                except DomainError as error:
                    new_status, outcome, reasons = "failed", None, [error.code]
                repo.finish(connection, run_id, new_status, outcome, summary, now, reasons)
            else:
                repo.finish(
                    connection,
                    run_id,
                    status.status,
                    None,
                    "Existing runtime terminal state reconciled",
                    now,
                    [status.reason_code] if status.reason_code else [],
                )
        else:
            connection.execute(
                "UPDATE collection_runs SET status='queued',phase='queued',"
                "version=version+1,finished_at=NULL,cancellation_requested_at=NULL WHERE id=%s",
                (run_id,),
            )
            repo.add_event(
                connection, run_id, "recovered", "Safe pre-submission task requeued", now
            )
        if run["account_id"] is not None:
            append_event(
                connection,
                run["account_id"],
                "run_reconciled",
                caller.actor_kind,
                status.status if status else "queued",
                now,
                run_id,
            )
        result = RunSnapshot.model_validate(repo.snapshot(connection, run_id)).model_dump(
            mode="json"
        )
        record(connection, scope, key, payload, result)
        return result
