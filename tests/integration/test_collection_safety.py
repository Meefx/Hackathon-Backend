from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.config import get_settings
from app.db.connection import connect
from app.modules.audit.service import verify_chain
from app.modules.conversations.schemas import DispatchRequest
from app.modules.conversations.service import simulate_dispatch
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run
from app.seed import seed
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.workers.collection import claim, process_once

NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)


def draft(database_url):
    seed()
    settings = get_settings()
    accepted = start_run(
        settings,
        "CF-1001",
        StartRunRequest(instruction="Pengingat", idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    assert process_once(settings, now=NOW)
    with connect(database_url) as conn:
        return conn.execute(
            "SELECT * FROM collection_messages WHERE run_id=%s", (accepted["run_id"],)
        ).fetchone()


def test_dispatch_race_receipt_unique_and_hash_chain(database_url):
    message = draft(database_url)

    def dispatch(_):
        try:
            return simulate_dispatch(
                get_settings(),
                message["id"],
                DispatchRequest(expected_message_version=1, idempotency_key=uuid4()),
                Caller(),
                NOW,
            )
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(dispatch, range(2)))
    assert sum(isinstance(r, dict) and r["status"] == "simulated_sent" for r in results) == 1
    with connect(database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 1
        events = conn.execute(
            "SELECT * FROM audit_events WHERE account_id=%s ORDER BY sequence", ("CF-1001",)
        ).fetchall()
    assert verify_chain(events)
    events[-1]["outcome"] = "tampered"
    assert not verify_chain(events)


def test_changed_content_and_expired_approval_never_dispatch(database_url):
    message = draft(database_url)
    with connect(database_url) as conn:
        conn.execute(
            "UPDATE collection_messages SET text=%s WHERE id=%s", ("tampered", message["id"])
        )
    result = simulate_dispatch(
        get_settings(),
        message["id"],
        DispatchRequest(expected_message_version=1, idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    assert result["status"] == "blocked" and "approval_invalid" in result["reason_codes"]
    result = simulate_dispatch(
        get_settings(),
        message["id"],
        DispatchRequest(expected_message_version=1, idempotency_key=uuid4()),
        Caller(),
        NOW + timedelta(hours=1),
    )
    assert "approval_expired" in result["reason_codes"]


def test_missing_recipient_blocks_before_generation(database_url):
    seed()
    with connect(database_url) as conn:
        conn.execute(
            "UPDATE collection_cases SET recipient_verified=FALSE WHERE account_id='CF-1001'"
        )
    run = start_run(
        get_settings(),
        "CF-1001",
        StartRunRequest(instruction="Reminder", idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    process_once(now=NOW)
    with connect(database_url) as conn:
        row = conn.execute("SELECT * FROM collection_runs WHERE id=%s", (run["run_id"],)).fetchone()
        assert row["status"] == "blocked" and "recipient_unverified" in row["reason_codes"]
        assert not conn.execute("SELECT * FROM collection_messages").fetchall()


def test_expired_worker_cannot_commit_and_interrupted_session_holds_next(database_url):
    seed()
    settings = get_settings()
    one = start_run(
        settings,
        "CF-1001",
        StartRunRequest(instruction="One", idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    start_run(
        settings,
        "CF-1001",
        StartRunRequest(
            instruction="Two", idempotency_key=uuid4(), manager_session_id=one["manager_session_id"]
        ),
        Caller(),
        NOW,
    )
    claimed = claim(settings, NOW, "worker-one")
    assert claimed[0]["id"] == one["run_id"]
    assert claim(settings, NOW + timedelta(seconds=120), "worker-two") is None
    from app.modules.runs.repository import verify_fence

    with connect(database_url) as conn, pytest.raises(DomainError, match="lease"):
        verify_fence(conn, one["run_id"], claimed[1], claimed[2], NOW + timedelta(seconds=120))
    with connect(database_url) as conn:
        rows = conn.execute(
            "SELECT id,status FROM collection_runs ORDER BY session_turn_index"
        ).fetchall()
        assert [r["status"] for r in rows] == ["interrupted", "queued"]


@pytest.mark.parametrize(
    "task,kind,role",
    [
        ("collector_brief", "collector_brief", "copilot"),
        ("improvement_review", "improvement_proposal", "improvement"),
    ],
)
def test_specialist_tasks_persist_advisory_artifacts(database_url, task, kind, role):
    seed()
    run = start_run(
        get_settings(),
        "CF-1001",
        StartRunRequest(
            instruction="Tinjau kasus sintetis", task_type=task, idempotency_key=uuid4()
        ),
        Caller(),
        NOW,
    )
    assert process_once(now=NOW)
    with connect(database_url) as connection:
        result = connection.execute(
            "SELECT * FROM collection_runs WHERE id=%s", (run["run_id"],)
        ).fetchone()
        assert result["status"] == "completed"
        artifact = connection.execute(
            "SELECT * FROM run_artifacts WHERE run_id=%s", (run["run_id"],)
        ).fetchone()
        assert artifact["kind"] == kind
        if task == "improvement_review":
            assert artifact["review_state"] == "pending"
        roles = {
            r["role"]
            for r in connection.execute(
                "SELECT role FROM run_steps WHERE run_id=%s", (run["run_id"],)
            ).fetchall()
        }
        assert {"manager", "knowledge", role} <= roles
        assert (
            connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 0
        )


def test_missing_required_knowledge_stops_before_candidate(database_url, monkeypatch):
    seed()
    monkeypatch.setenv("KNOWLEDGE_MODE", "unavailable")
    get_settings.cache_clear()
    run = start_run(
        get_settings(),
        "CF-1001",
        StartRunRequest(instruction="Reminder", idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    process_once(now=NOW)
    with connect(database_url) as connection:
        result = connection.execute(
            "SELECT * FROM collection_runs WHERE id=%s", (run["run_id"],)
        ).fetchone()
        assert result["status"] == "blocked"
        assert "knowledge_unavailable" in result["reason_codes"]
        assert (
            connection.execute("SELECT count(*) AS n FROM collection_messages").fetchone()["n"] == 0
        )
