from uuid import uuid4

from psycopg.types.json import Jsonb

from app.db.connection import connect
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.runs import repository as repo
from app.shared.errors import DomainError
from app.shared.idempotency import record, replay


def start_run(settings, account_id, request, caller, now):
    payload = request.model_dump(mode="json")
    key = str(request.idempotency_key)
    scope = "start_run:" + account_id + ":" + caller.principal_id
    with connect(settings.database_url) as connection:
        lock_case(connection, account_id)
        if old := replay(connection, scope, key, payload):
            return old
        session_id = str(request.manager_session_id or uuid4())
        if request.manager_session_id:
            session = connection.execute(
                "SELECT * FROM manager_sessions WHERE id=%s FOR UPDATE", (session_id,)
            ).fetchone()
            if (
                not session
                or session["account_id"] != account_id
                or session["caller_id"] != caller.principal_id
            ):
                raise DomainError(
                    "session_denied", "Session does not belong to this account/caller", 403
                )
        else:
            connection.execute(
                "INSERT INTO manager_sessions(id,account_id,caller_id,title,"
                "created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (session_id, account_id, caller.principal_id, request.instruction[:80], now, now),
            )
        session = connection.execute(
            "UPDATE manager_sessions SET next_turn=next_turn+1 "
            "WHERE id=%s RETURNING next_turn-1 AS turn",
            (session_id,),
        ).fetchone()
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
                session["turn"],
                now,
            ),
        )
        connection.execute("INSERT INTO collection_jobs(run_id) VALUES (%s)", (run_id,))
        connection.execute(
            "INSERT INTO manager_turns VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid4()),
                session_id,
                session["turn"],
                run_id,
                "operator",
                request.instruction,
                now,
            ),
        )
        connection.execute(
            "UPDATE manager_sessions SET last_run_id=%s,updated_at=%s WHERE id=%s",
            (run_id, now, session_id),
        )
        repo.add_event(connection, run_id, "queued", "Task admitted to durable queue", now)
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
                if not status.final_result:
                    raise DomainError(
                        "result_invalid", "Completed runtime lacks a valid final result"
                    )
                new_status, outcome, reasons = validate_final(connection, run, status.final_result)
                repo.finish(
                    connection,
                    run_id,
                    new_status,
                    outcome,
                    status.final_result.summary,
                    now,
                    reasons,
                )
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
