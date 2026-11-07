from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.config import get_settings
from app.db.connection import connect
from app.integrations.agents.contracts import RuntimeError, RuntimeProbe, RuntimeStatus
from app.modules.runs.repository import require_run
from app.modules.runs.schemas import RecoverRunRequest, StartRunRequest
from app.modules.runs.service import recover_run, start_run
from app.seed import seed
from app.shared.caller import Caller
from app.workers import collection as worker

NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)


def session_tasks(monkeypatch):
    seed()
    settings = get_settings()
    # Isolate runtime lifecycle from the PR1 knowledge adapter currently being integrated.
    monkeypatch.setattr(
        worker,
        "prepare_knowledge",
        lambda *_args, **_kwargs: {"status": "ready", "sources": [{"citation_id": "fixture"}]},
    )
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)
    first = start_run(
        settings,
        "CF-1001",
        StartRunRequest(
            instruction="Synthetic brief",
            task_type="collector_brief",
            runtime_mode="hermes",
            idempotency_key=uuid4(),
        ),
        Caller(),
        NOW,
    )
    second = start_run(
        settings,
        "CF-1001",
        StartRunRequest(
            instruction="Next task",
            task_type="collector_brief",
            runtime_mode="hermes",
            idempotency_key=uuid4(),
            manager_session_id=first["manager_session_id"],
        ),
        Caller(),
        NOW,
    )
    return settings, first, second


def test_definitive_admission_rejection_releases_session(database_url, monkeypatch):
    settings, first, second = session_tasks(monkeypatch)

    class RejectedRuntime:
        def probe(self):
            return RuntimeProbe(available=True, actual_runtime_mode="hermes")

        def submit(self, *_args):
            # The HTTP adapter maps definitive401/403 to this nonambiguous contract.
            raise RuntimeError("runtime_auth_rejected", ambiguous=False)

    monkeypatch.setattr(
        "app.integrations.hermes.runtime.create_hermes_runtime", lambda _: RejectedRuntime()
    )
    worker.process_once(settings, now=NOW)
    with connect(database_url) as connection:
        result = require_run(connection, first["run_id"])
        assert result["status"] == "blocked"
        assert result["reason_codes"] == ["runtime_auth_rejected"]
        assert result["external_run_id"] is None
    assert worker.claim(settings, NOW, "next-worker")[0]["id"] == second["run_id"]


@pytest.mark.parametrize("entry_point", ["worker", "reconcile"])
def test_conclusive_invalid_final_result_fails_and_releases_session(
    database_url, monkeypatch, entry_point
):
    settings, first, second = session_tasks(monkeypatch)

    class CompletedRuntime:
        def probe(self):
            return RuntimeProbe(available=True, actual_runtime_mode="hermes")

        def submit(self, *_args):
            from app.integrations.agents.contracts import RuntimeSubmission

            return RuntimeSubmission(
                external_run_id="conclusive", actual_runtime_mode="hermes", status="running"
            )

        def get_status(self, external_id):
            return RuntimeStatus(
                external_run_id=external_id,
                actual_runtime_mode="hermes",
                status="completed",
                final_result={
                    "application_run_id": first["run_id"],
                    "task_type": "collector_brief",
                    "summary": "References do not belong to this run",
                    "artifact_ids": ["missing-artifact"],
                    "citation_ids": ["missing-citation"],
                    "review_requested": False,
                },
            )

    monkeypatch.setattr(
        "app.integrations.hermes.runtime.create_hermes_runtime", lambda _: CompletedRuntime()
    )
    if entry_point == "worker":
        worker.process_once(settings, now=NOW)
    else:
        with connect(database_url) as connection:
            connection.execute(
                "UPDATE collection_runs SET status='interrupted',"
                "external_run_id='conclusive' WHERE id=%s",
                (first["run_id"],),
            )
            version = require_run(connection, first["run_id"])["version"]
        request = RecoverRunRequest(
            action="reconcile", expected_version=version, idempotency_key=uuid4()
        )
        result = recover_run(settings, first["run_id"], request, Caller(), lambda: NOW)
        assert result["status"] == "failed"
        assert recover_run(settings, first["run_id"], request, Caller(), lambda: NOW) == result
    with connect(database_url) as connection:
        result = require_run(connection, first["run_id"])
        assert result["status"] == "failed"
        assert result["reason_codes"] == ["knowledge_reference_invalid"]
        assert result["external_run_id"] == "conclusive"
    assert worker.claim(settings, NOW, "next-worker")[0]["id"] == second["run_id"]
