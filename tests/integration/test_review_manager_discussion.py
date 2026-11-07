from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.db.connection import connect
from app.modules.reviews.service import open_review
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run
from app.seed import seed
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.shared.idempotency import record
from app.workers.collection import process_once

NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)


def discussion(review_id, **overrides):
    return StartRunRequest(
        instruction="Mengapa kasus ini perlu ditinjau?",
        task_type="collector_brief",
        review_id=review_id,
        idempotency_key=uuid4(),
        **overrides,
    )


def test_review_discussion_requires_non_outbound_task():
    with pytest.raises(ValidationError, match="collector_brief"):
        StartRunRequest(
            instruction="Discuss review", review_id=uuid4(), idempotency_key=uuid4()
        )


def test_legacy_run_retry_preserves_original_payload_hash(database_url):
    seed()
    request = StartRunRequest(instruction="Existing task", idempotency_key=uuid4())
    legacy_payload = request.model_dump(mode="json", exclude={"review_id"})
    accepted = {"run_id": "previous-run", "status": "queued"}
    with connect(database_url) as connection:
        record(
            connection, "start_run:CF-1001:local-operator", str(request.idempotency_key),
            legacy_payload, accepted,
        )
    assert start_run(get_settings(), "CF-1001", request, Caller(), NOW) == accepted


def test_discussion_worker_cannot_submit_outbound_draft(database_url):
    from app.modules.conversations.schemas import DraftCandidate
    from app.modules.conversations.service import submit_draft
    from app.workers.collection import claim

    seed()
    with connect(database_url) as connection:
        review_id = open_review(connection, "CF-1001", "hardship", NOW)
    settings = get_settings()
    accepted = start_run(settings, "CF-1001", discussion(review_id), Caller(), NOW)
    run, attempt, fence = claim(settings, NOW, "discussion-worker")
    caller = Caller(
        principal_id="local-worker", actor_kind="worker", account_id="CF-1001",
        run_id=run["id"], execution_attempt_id=attempt, fence=fence,
    )
    with pytest.raises(DomainError, match="advisory"):
        submit_draft(
            settings,
            DraftCandidate(
                run_id=accepted["run_id"], account_id="CF-1001", execution_attempt_id=attempt,
                text_with_placeholders="Pengingat {outstanding_amount}",
                citation_ids=["unneeded-evidence"], idempotency_key=uuid4(),
            ),
            caller, NOW,
        )


def test_review_discussion_rejects_other_account_atomically(database_url):
    seed()
    with connect(database_url) as connection:
        review_id = open_review(connection, "CF-1001", "hardship", NOW)
    with pytest.raises(DomainError, match="review"):
        start_run(get_settings(), "CF-1002", discussion(review_id), Caller(), NOW)
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 0


def test_paused_review_discussion_keeps_context_and_never_dispatches(database_url):
    seed()
    with connect(database_url) as connection:
        review_id = open_review(connection, "CF-1001", "hardship", NOW)
    settings = get_settings()
    first = start_run(settings, "CF-1001", discussion(review_id), Caller(), NOW)
    assert process_once(settings, now=NOW)
    with connect(database_url) as connection:
        run = connection.execute(
            "SELECT * FROM collection_runs WHERE id=%s", (first["run_id"],)
        ).fetchone()
        answer = connection.execute(
            "SELECT content FROM manager_turns WHERE run_id=%s AND role='manager'",
            (first["run_id"],),
        ).fetchone()["content"]
        assert run["status"] == "completed"
        assert "hardship" in answer
        assert "CF-1001" in answer
        assert "paused_human_review" in answer
        assert "simulasi" in answer.lower()
        assert connection.execute("SELECT count(*) AS n FROM collection_messages").fetchone()["n"] == 0
        assert connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 0
        assert connection.execute(
            "SELECT status FROM collection_reviews WHERE id=%s", (review_id,)
        ).fetchone()["status"] == "open"
    second = start_run(
        settings,
        "CF-1001",
        discussion(review_id, manager_session_id=first["manager_session_id"]),
        Caller(),
        NOW,
    )
    assert second["manager_session_id"] == first["manager_session_id"]
    assert process_once(settings, now=NOW)
    with connect(database_url) as connection:
        assert connection.execute(
            "SELECT count(*) AS n FROM manager_turns WHERE session_id=%s",
            (first["manager_session_id"],),
        ).fetchone()["n"] == 4


def test_worker_cycle_runs_scheduled_manager_and_operator_can_read_history(database_url):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.workers.collection import process_cycle

    seed()
    with connect(database_url) as connection:
        connection.execute("UPDATE accounts SET outstanding_amount=0 WHERE id!='CF-1001'")
    settings = get_settings()
    assert process_cycle(settings, now=NOW)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/accounts/CF-1001/runs")
        assert response.status_code == 200
        run = response.json()["items"][0]
        assert run["origin"] == "scheduled"
        sessions = client.get("/api/v1/accounts/CF-1001/manager-sessions").json()["items"]
        assert sessions[0]["origin"] == "scheduled"
        turns = client.get(
            f"/api/v1/manager-sessions/{run['manager_session_id']}/messages"
        ).json()["items"]
        assert [turn["role"] for turn in turns] == ["scheduler", "manager"]
        assert client.get("/api/v1/collection-automation/status").json()["counts"]["sent"] == 1
        request = StartRunRequest(
            instruction="Discuss the case", task_type="collector_brief",
            manager_session_id=run["manager_session_id"], idempotency_key=uuid4(),
        )
        assert client.post(
            "/api/v1/accounts/CF-1001/runs", json=request.model_dump(mode="json")
        ).status_code == 403
    assert not process_cycle(settings, now=NOW)
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 1


@pytest.mark.parametrize("runtime_mode", ["simulated", "hermes"])
def test_new_review_can_arrive_while_manager_loads_case_context(
    database_url, monkeypatch, runtime_mode
):
    from app.modules.cases.repository import lock_case
    from app.workers import collection as worker
    from app.workflows import collection as workflow

    seed()
    settings = get_settings()
    with connect(database_url) as connection:
        review_id = open_review(connection, "CF-1001", "hardship", NOW)
    start_run(
        settings, "CF-1001", discussion(review_id, runtime_mode=runtime_mode), Caller(), NOW
    )
    run, attempt, fence = worker.claim(settings, NOW, "discussion-worker")
    context_entered = Event()
    account_locked = Event()
    module = workflow if runtime_mode == "simulated" else worker
    original_context = module.discussion_context

    def concurrent_context(*args):
        context_entered.set()
        assert account_locked.wait(5)
        return original_context(*args)

    monkeypatch.setattr(module, "discussion_context", concurrent_context)
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)

    class AvailableRuntime:
        def probe(self):
            from app.integrations.agents.contracts import RuntimeProbe

            return RuntimeProbe(available=True, actual_runtime_mode="hermes")

    monkeypatch.setattr(
        "app.integrations.hermes.runtime.create_hermes_runtime", lambda _: AvailableRuntime()
    )
    caller = Caller(
        principal_id="local-worker", actor_kind="worker", account_id="CF-1001",
        run_id=run["id"], execution_attempt_id=attempt, fence=fence,
    )
    snapshot = workflow.prepare_knowledge(settings, run, NOW, caller)

    def discuss():
        if runtime_mode == "simulated":
            workflow.execute_simulated(settings, run, attempt, fence, NOW, snapshot)
        else:
            worker.execute_hermes(settings, run, attempt, fence, NOW, {})

    def new_review():
        assert context_entered.wait(5)
        with connect(database_url) as connection:
            lock_case(connection, "CF-1001")
            account_locked.set()
            open_review(connection, "CF-1001", "dispute", NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(discuss), pool.submit(new_review)]
        for future in futures:
            future.result(timeout=15)
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_reviews").fetchone()["n"] == 2
        assert connection.execute("SELECT status FROM collection_runs").fetchone()["status"] == "cancelled"
