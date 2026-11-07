import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.integrations.agents.contracts import FinalRuntimeResult, RuntimeRequest
from app.integrations.hermes.client import HermesClient

NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)


def chat_request():
    return RuntimeRequest(
        application_run_id=str(uuid4()), account_id=None, context_account_id="CF-1001",
        execution_attempt_id=str(uuid4()), fence=1, task_type="manager_chat",
        instruction="Summarize the portfolio and inspect the selected account.",
        history=[{"role": "system", "content": "Use assigned portfolio MCP tools."}],
    )


def test_portfolio_runtime_has_explicit_scope_without_fake_account():
    request = chat_request()
    assert request.account_id is None
    assert request.context_account_id == "CF-1001"
    with pytest.raises(ValidationError):
        RuntimeRequest(**{**request.model_dump(), "task_type": "follow_up"})
    with pytest.raises(ValidationError):
        RuntimeRequest(**{**request.model_dump(), "account_id": "CF-1001"})


def test_portfolio_hermes_submission_instructs_scoped_tools_and_advisory_actions():
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(202, json={"run_id": "portfolio-runtime", "status": "started"})

    runtime = HermesClient(
        "https://runtime.example", "fixture-secret", transport=httpx.MockTransport(handle)
    )
    runtime.submit(chat_request(), "portfolio-stable-key")
    instructions = seen[0]["instructions"]
    assert "get_portfolio_overview" in instructions
    assert "inspect_portfolio_account" in instructions
    assert "propose_account_task" in instructions
    assert "submit_manager_response" in instructions
    assert '"account_id": null' in instructions
    assert "operator" in instructions.lower()


def test_portfolio_final_result_is_accepted_by_runtime_adapter():
    request = chat_request()
    result = FinalRuntimeResult(
        application_run_id=request.application_run_id, task_type="manager_chat",
        summary="Portfolio analysis", artifact_ids=[str(uuid4())], citation_ids=[],
        candidate_message_id=None, review_requested=False,
    )
    runtime = HermesClient(
        "https://runtime.example", "fixture-secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "run_id": "portfolio-runtime", "status": "completed", "output": result.model_dump_json(),
        })),
    )
    status = runtime.get_status("portfolio-runtime")
    assert status.status == "completed"
    assert status.final_result.task_type == "manager_chat"


def start_chat(settings, **overrides):
    from app.modules.manager.chat_schemas import ManagerChatRequest
    from app.modules.manager.chat_service import ManagerChatService
    from app.shared.caller import Caller

    payload = {"instruction": "What needs attention?", "idempotency_key": uuid4()}
    payload.update(overrides)
    return ManagerChatService(settings, lambda: NOW).start_run(
        ManagerChatRequest(**payload), Caller()
    )


def test_simulated_portfolio_chat_runs_through_existing_worker_without_outreach(database_url):
    from app.config import get_settings
    from app.db.connection import connect
    from app.modules.runs.repository import snapshot
    from app.seed import seed
    from app.workers.collection import process_once

    seed()
    settings = get_settings()
    accepted = start_chat(settings, context_account_id="CF-1001")
    assert process_once(settings, now=NOW)
    with connect(database_url) as connection:
        run = snapshot(connection, accepted["run_id"])
        assert run["status"] == "completed"
        assert run["scope"] == "portfolio" and run["account_id"] is None
        assert run["context_account_id"] == "CF-1001"
        response = next(a for a in run["artifacts"] if a["kind"] == "manager_response")
        assert "CF-1001" in response["text"]
        assert "simulat" in response["text"].lower()
        assert any(a["kind"] == "manager_task_proposal" for a in run["artifacts"])
        assert connection.execute("SELECT count(*) AS n FROM collection_messages").fetchone()["n"] == 0
        assert connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 0
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 1


def test_hermes_portfolio_chat_receives_history_and_finishes_with_persisted_reply(
    database_url, monkeypatch
):
    from app.config import get_settings
    from app.db.connection import connect
    from app.integrations.agents.contracts import RuntimeProbe, RuntimeStatus, RuntimeSubmission
    from app.modules.manager.chat_service import ManagerChatService
    from app.modules.runs.repository import snapshot
    from app.seed import seed
    from app.shared.caller import Caller
    from app.workers import collection as worker

    seed()
    settings = get_settings()
    requests = []
    artifacts = {}

    class FixtureRuntime:
        def probe(self):
            return RuntimeProbe(available=True, actual_runtime_mode="hermes")

        def submit(self, request, key):
            requests.append(request)
            caller = Caller(
                principal_id=settings.mcp_principal_id, actor_kind="agent", account_id=None,
                run_id=request.application_run_id, execution_attempt_id=request.execution_attempt_id,
                fence=request.fence,
            )
            response = ManagerChatService(settings, lambda: NOW).submit_response(
                request.application_run_id, "The portfolio has overdue accounts.", caller, uuid4()
            )
            artifacts[request.application_run_id] = response["id"]
            return RuntimeSubmission(
                external_run_id=request.application_run_id, actual_runtime_mode="hermes", status="running"
            )

        def get_status(self, run_id):
            return RuntimeStatus(
                external_run_id=run_id, actual_runtime_mode="hermes", status="completed",
                final_result=FinalRuntimeResult(
                    application_run_id=run_id, task_type="manager_chat",
                    summary="The portfolio has overdue accounts.", artifact_ids=[artifacts[run_id]],
                    citation_ids=[], review_requested=False,
                ),
            )

    monkeypatch.setattr("app.integrations.hermes.runtime.create_hermes_runtime", lambda _: FixtureRuntime())
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)
    first = start_chat(settings, runtime_mode="hermes")
    assert worker.process_once(settings, now=NOW)
    second = start_chat(settings, runtime_mode="hermes", manager_session_id=first["manager_session_id"], context_account_id="CF-1001")
    assert worker.process_once(settings, now=NOW)
    assert requests[1].account_id is None
    assert requests[1].context_account_id == "CF-1001"
    assert any(message.role == "assistant" and message.content == "The portfolio has overdue accounts." for message in requests[1].history)
    with connect(database_url) as connection:
        assert snapshot(connection, second["run_id"])["status"] == "completed"


@pytest.mark.parametrize("invalid", ["different_summary", "outbound", "review", "foreign_artifact"])
def test_portfolio_final_result_requires_matching_advisory_evidence(database_url, invalid):
    from app.config import get_settings
    from app.db.connection import connect
    from app.modules.manager.chat_service import ManagerChatService
    from app.seed import seed
    from app.shared.caller import Caller
    from app.shared.errors import DomainError
    from app.workers.collection import claim
    from app.workflows.collection import validate_final

    seed()
    settings = get_settings()
    accepted = start_chat(settings)
    run, attempt, fence = claim(settings, NOW, "portfolio-fixture")
    caller = Caller(
        principal_id="local-worker", actor_kind="worker", run_id=run["id"],
        execution_attempt_id=attempt, fence=fence,
    )
    artifact = ManagerChatService(settings, lambda: NOW).submit_response(
        run["id"], "Saved answer", caller, uuid4()
    )
    result = {
        "application_run_id": accepted["run_id"], "task_type": "manager_chat",
        "summary": "Saved answer", "artifact_ids": [artifact["id"]], "citation_ids": [],
        "review_requested": False,
    }
    changes = {
        "different_summary": {"summary": "Unsaved answer"},
        "outbound": {"candidate_message_id": "outbound-claim"},
        "review": {"review_requested": True},
        "foreign_artifact": {"artifact_ids": [str(uuid4())]},
    }
    result.update(changes[invalid])
    with connect(database_url) as connection, pytest.raises(DomainError, match="Chat|chat|Portfolio"):
        validate_final(connection, run, FinalRuntimeResult(**result))
