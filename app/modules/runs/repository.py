from uuid import uuid4

from psycopg.types.json import Jsonb

from app.shared.errors import DomainError


def require_run(connection, run_id, lock=False):
    row = connection.execute(
        "SELECT * FROM collection_runs WHERE id=%s" + (" FOR UPDATE" if lock else ""), (run_id,)
    ).fetchone()
    if not row:
        raise DomainError("not_found", "Run not found", 404)
    return row


def add_event(connection, run_id, event_type, summary, now, origin="backend_verified"):
    row = connection.execute(
        "UPDATE collection_runs SET latest_event_seq=latest_event_seq+1 "
        "WHERE id=%s RETURNING latest_event_seq",
        (run_id,),
    ).fetchone()
    connection.execute(
        "INSERT INTO run_events VALUES (%s,%s,%s,%s,%s,%s)",
        (run_id, row["latest_event_seq"], event_type, summary, origin, now),
    )


def snapshot(connection, run_id):
    row = require_run(connection, run_id)
    row["origin"] = (
        "scheduled" if row["caller_id"] == "daily-collection-scheduler" else "operator"
    )
    row["scope"] = "portfolio" if row["account_id"] is None else "account"
    row["context_account_id"] = row["payload"].get("context_account_id")
    row["steps"] = connection.execute(
        "SELECT * FROM run_steps WHERE run_id=%s ORDER BY sequence", (run_id,)
    ).fetchall()
    row["artifacts"] = connection.execute(
        "SELECT * FROM run_artifacts WHERE run_id=%s ORDER BY created_at,id", (run_id,)
    ).fetchall()
    row["allowed_actions"] = (
        ["cancel"]
        if row["status"] in ("queued", "running")
        else ["reconcile"]
        if row["status"] == "interrupted"
        else []
    )
    return row


def check_assignment(connection, caller, account_id, run_id=None, now=None, allow_inactive=False):
    if caller.actor_kind == "operator":
        return
    if caller.account_id != account_id or not run_id or caller.run_id != run_id:
        raise DomainError("assignment_denied", "Account assignment denied", 403)
    row = connection.execute(
        "SELECT r.account_id,r.status,r.cancellation_requested_at,j.* "
        "FROM collection_runs r JOIN collection_jobs j ON j.run_id=r.id WHERE r.id=%s",
        (run_id,),
    ).fetchone()
    from app.config import get_settings
    from app.shared.clock import utcnow

    trusted_principal = (
        "local-worker" if caller.actor_kind == "worker" else get_settings().mcp_principal_id
    )
    if (
        not row
        or caller.principal_id != trusted_principal
        or row["account_id"] != account_id
        or row["execution_attempt_id"] != caller.execution_attempt_id
        or row["fence"] != caller.fence
    ):
        raise DomainError("assignment_denied", "Execution ownership required", 403)
    if not allow_inactive and (
        row["status"] != "running"
        or row["cancellation_requested_at"]
        or not row["lease_until"]
        or row["lease_until"] <= (now or utcnow())
    ):
        raise DomainError("assignment_denied", "Active execution assignment required", 403)


def verify_fence(connection, run_id, attempt, fence, now):
    row = connection.execute(
        "SELECT r.status,r.cancellation_requested_at,j.* FROM collection_runs r "
        "JOIN collection_jobs j ON j.run_id=r.id WHERE r.id=%s FOR UPDATE OF r,j",
        (run_id,),
    ).fetchone()
    if (
        not row
        or row["status"] != "running"
        or row["execution_attempt_id"] != attempt
        or row["fence"] != fence
        or row["lease_until"] <= now
    ):
        raise DomainError("stale_worker", "Worker lease is no longer valid")
    return row


def add_step(connection, run_id, role, summary, now, origin="backend_verified"):
    sequence = connection.execute(
        "SELECT count(*)+1 AS n FROM run_steps WHERE run_id=%s", (run_id,)
    ).fetchone()["n"]
    connection.execute(
        "INSERT INTO run_steps VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (str(uuid4()), run_id, sequence, role, "completed", summary, origin, now, now),
    )
    add_event(connection, run_id, "step_completed", summary, now, origin)


def finish(connection, run_id, status, outcome, summary, now, reasons=None):
    row = require_run(connection, run_id, True)
    connection.execute(
        "UPDATE collection_runs SET status=%s,outcome=%s,phase=%s,reason_codes=%s,"
        "version=version+1,finished_at=%s WHERE id=%s",
        (status, outcome, status, Jsonb(reasons or []), now, run_id),
    )
    connection.execute(
        "INSERT INTO manager_turns VALUES (%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(session_id,turn_index,role) DO UPDATE SET content=EXCLUDED.content, created_at=EXCLUDED.created_at",
        (
            str(uuid4()),
            row["manager_session_id"],
            row["session_turn_index"],
            run_id,
            "manager",
            summary,
            now,
        ),
    )
    connection.execute(
        "UPDATE manager_sessions SET updated_at=%s WHERE id=%s", (now, row["manager_session_id"])
    )
    add_event(connection, run_id, status, summary, now)
