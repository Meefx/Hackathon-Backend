from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.config import get_settings
from app.db.connection import connect
from app.integrations.agents.contracts import RuntimeProbe, RuntimeStatus, RuntimeSubmission
from app.integrations.knowledge.gateway import DemoKnowledgeGateway
from app.modules.cases.schemas import CaseActionRequest
from app.modules.conversations.schemas import DispatchRequest
from app.modules.conversations.service import simulate_dispatch
from app.modules.knowledge_context.contracts import KnowledgeResult, KnowledgeSource
from app.modules.knowledge_context.service import persist_snapshot
from app.modules.reviews.schemas import ReviewResolution
from app.modules.reviews.service import case_action, open_review, resolve_review
from app.modules.runs.repository import add_step, snapshot
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run
from app.seed import seed
from app.shared.caller import Caller
from app.shared.idempotency import digest
from app.workers import collection as worker

NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)


def started(settings, task="follow_up", session=None):
    return start_run(
        settings,
        "CF-1001",
        StartRunRequest(
            instruction="Synthetic review",
            task_type=task,
            idempotency_key=uuid4(),
            manager_session_id=session,
        ),
        Caller(),
        NOW,
    )


def ready_draft(database_url):
    seed()
    run = started(get_settings())
    worker.process_once(now=NOW)
    with connect(database_url) as connection:
        message = connection.execute(
            "SELECT * FROM collection_messages WHERE run_id=%s", (run["run_id"],)
        ).fetchone()
    return run, message


def test_dispatch_rejects_interrupted_or_cancelling_origin(database_url):
    run, message = ready_draft(database_url)
    with connect(database_url) as connection:
        connection.execute(
            "UPDATE collection_runs SET status='interrupted' WHERE id=%s", (run["run_id"],)
        )
    result = simulate_dispatch(
        get_settings(),
        message["id"],
        DispatchRequest(expected_message_version=1, idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    assert result["status"] == "blocked"
    assert "run_not_accepted" in result["reason_codes"]


def test_each_review_retains_its_own_hold_until_explicit_case_resume(database_url):
    seed()
    with connect(database_url) as connection:
        first = open_review(connection, "CF-1001", "hardship", NOW)
        second = open_review(connection, "CF-1001", "dispute", NOW)
    result = resolve_review(
        get_settings(),
        first,
        ReviewResolution(
            decision="keep_paused",
            expected_version=1,
            note="Keep first hold",
            idempotency_key=uuid4(),
        ),
        Caller(),
        NOW,
    )
    result = resolve_review(
        get_settings(),
        second,
        ReviewResolution(
            decision="request_resume",
            expected_version=1,
            note="Resolve second only",
            idempotency_key=uuid4(),
        ),
        Caller(),
        NOW,
    )
    assert result["case"]["contact_state"] == "paused_operator"
    assert not result["case"]["action_applied"]
    resumed = case_action(
        get_settings(),
        "CF-1001",
        CaseActionRequest(
            expected_case_version=result["case"]["case_version"],
            note="Explicit operator resume",
            idempotency_key=uuid4(),
        ),
        Caller(),
        NOW,
    )
    assert resumed["contact_state"] == "active"


def test_unrelated_knowledge_retrieval_does_not_change_approved_sources(database_url):
    run, message = ready_draft(database_url)
    source = KnowledgeSource(
        document_id="other",
        document_version="1",
        snippet_id="other",
        citation_id="other",
        title="Other SOP",
        excerpt="Unrelated approved context",
        content_hash=digest("Unrelated approved context"),
        approved_at=NOW - timedelta(days=1),
    )
    with connect(database_url) as connection:
        persist_snapshot(
            connection,
            run["run_id"],
            KnowledgeResult(status="ready", source_mode="demo_fixture", sources=[source]),
            NOW + timedelta(seconds=1),
        )
    result = simulate_dispatch(
        get_settings(),
        message["id"],
        DispatchRequest(expected_message_version=1, idempotency_key=uuid4()),
        Caller(),
        NOW + timedelta(seconds=2),
    )
    assert result["status"] == "simulated_sent", result


def test_observable_progress_always_advances_snapshot_version(database_url):
    seed()
    settings = get_settings()
    run = started(settings)
    _claimed, attempt, fence = worker.claim(settings, NOW, "worker")
    with connect(database_url) as connection:
        previous = snapshot(connection, run["run_id"])["version"]
        add_step(connection, run["run_id"], "manager", "Visible progress", NOW)
        assert snapshot(connection, run["run_id"])["version"] > previous
        previous = snapshot(connection, run["run_id"])["version"]
    worker.heartbeat(settings, run["run_id"], attempt, fence, NOW + timedelta(seconds=1))
    with connect(database_url) as connection:
        assert snapshot(connection, run["run_id"])["version"] > previous
        previous = snapshot(connection, run["run_id"])["version"]
        connection.execute(
            "UPDATE collection_runs SET phase='provider' WHERE id=%s", (run["run_id"],)
        )
        assert snapshot(connection, run["run_id"])["version"] > previous
        previous = snapshot(connection, run["run_id"])["version"]
        connection.execute(
            "INSERT INTO run_artifacts VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid4()),
                run["run_id"],
                "qa_report",
                "Visible artifact",
                None,
                Jsonb([]),
                None,
                NOW,
            ),
        )
        assert snapshot(connection, run["run_id"])["version"] > previous


class Runtime:
    def probe(self):
        return RuntimeProbe(available=True, actual_runtime_mode="hermes")

    def submit(self, request, key):
        return RuntimeSubmission(
            external_run_id="admitted", actual_runtime_mode="hermes", status="running"
        )

    def get_status(self, external_id):
        return RuntimeStatus(
            external_run_id=external_id, actual_runtime_mode="hermes", status="cancelled"
        )


def hermes_setup(database_url, monkeypatch, runtime):
    seed()
    settings = get_settings()
    run = start_run(
        settings,
        "CF-1001",
        StartRunRequest(
            instruction="Synthetic",
            task_type="collector_brief",
            runtime_mode="hermes",
            idempotency_key=uuid4(),
        ),
        Caller(),
        NOW,
    )
    monkeypatch.setattr("app.integrations.hermes.runtime.create_hermes_runtime", lambda _: runtime)
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)
    return settings, run


def test_db_failure_after_admission_keeps_identity_and_session_interrupted(
    database_url, monkeypatch
):
    runtime = Runtime()
    settings, run = hermes_setup(database_url, monkeypatch, runtime)
    start_run(
        settings,
        "CF-1001",
        StartRunRequest(
            instruction="Next",
            task_type="collector_brief",
            runtime_mode="hermes",
            manager_session_id=run["manager_session_id"],
            idempotency_key=uuid4(),
        ),
        Caller(),
        NOW,
    )
    original_submit = runtime.submit
    original_connect = worker.connect
    failed = [False]

    def submit(request, key):
        result = original_submit(request, key)

        @contextmanager
        def fail_once(url):
            if not failed[0]:
                failed[0] = True
                raise psycopg.OperationalError("synthetic connection failure")
            with original_connect(url) as connection:
                yield connection

        monkeypatch.setattr(worker, "connect", fail_once)
        return result

    runtime.submit = submit
    assert worker.process_once(settings, now=NOW)
    with connect(database_url) as connection:
        state = connection.execute(
            "SELECT * FROM collection_runs WHERE id=%s", (run["run_id"],)
        ).fetchone()
        assert state["status"] == "interrupted"
        assert state["external_run_id"] == "admitted"
    assert worker.claim(settings, NOW, "next-worker") is None


@pytest.mark.parametrize("change", ["lease_expiry", "cancel"])
def test_provider_return_rechecks_clock_and_locked_cancellation(database_url, monkeypatch, change):
    runtime = Runtime()
    settings, run = hermes_setup(database_url, monkeypatch, runtime)
    times = [NOW]
    monkeypatch.setattr(worker, "utcnow", lambda: times[0])

    def status(external_id):
        if change == "lease_expiry":
            times[0] = NOW + timedelta(seconds=120)
        else:
            with connect(database_url) as connection:
                connection.execute(
                    "UPDATE collection_runs SET cancellation_requested_at=%s WHERE id=%s",
                    (NOW, run["run_id"]),
                )
        return RuntimeStatus(
            external_run_id=external_id,
            actual_runtime_mode="hermes",
            status="completed",
            final_result={
                "application_run_id": run["run_id"],
                "task_type": "collector_brief",
                "summary": "Untrusted late completion",
                "artifact_ids": [],
                "citation_ids": [],
                "review_requested": False,
            },
        )

    runtime.get_status = status
    # Final evidence validation must never run for an expired/cancelled assignment.
    monkeypatch.setattr(worker, "validate_final", lambda *_: ("completed", "brief_created", []))
    worker.process_once(settings)
    with connect(database_url) as connection:
        result = connection.execute(
            "SELECT * FROM collection_runs WHERE id=%s", (run["run_id"],)
        ).fetchone()
        assert result["status"] != "completed"
        if change == "cancel":
            assert result["status"] == "interrupted"


def test_repeated_snapshot_cannot_rebind_original_citation(database_url):
    run, message = ready_draft(database_url)
    query = type("Query", (), {})()
    result = DemoKnowledgeGateway().query_approved(query, Caller())
    with connect(database_url) as connection:
        persist_snapshot(connection, run["run_id"], result, NOW)
        binding = connection.execute(
            "SELECT binding FROM message_approvals WHERE message_id=%s", (message["id"],)
        ).fetchone()["binding"]
        assert binding["knowledge_references"][0]["snapshot_id"]
    dispatched = simulate_dispatch(
        get_settings(),
        message["id"],
        DispatchRequest(expected_message_version=1, idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    assert dispatched["status"] == "simulated_sent"


def test_cancel_during_probe_never_admits_external_work(database_url, monkeypatch):
    runtime = Runtime()
    settings, run = hermes_setup(database_url, monkeypatch, runtime)

    def probe():
        with connect(database_url) as connection:
            connection.execute(
                "UPDATE collection_runs SET cancellation_requested_at=%s WHERE id=%s",
                (NOW, run["run_id"]),
            )
        return RuntimeProbe(available=True, actual_runtime_mode="hermes")

    runtime.probe = probe

    def submit(*_):
        pytest.fail("A cancelled task must never be admitted")

    runtime.submit = submit
    worker.process_once(settings, now=NOW)
    with connect(database_url) as connection:
        state = connection.execute(
            "SELECT status FROM collection_runs WHERE id=%s", (run["run_id"],)
        ).fetchone()["status"]
        assert state == "cancelled"
