from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.db.connection import connect
from app.modules.manager.chat_schemas import ManagerChatRequest
from app.modules.manager.chat_service import ManagerChatService
from app.modules.reviews.service import open_review
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run
from app.seed import seed
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.shared.idempotency import record

NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)


def request(**changes):
    return ManagerChatRequest(
        instruction="Ringkas portfolio dan prioritasnya", idempotency_key=uuid4(), **changes
    )


def active_chat(service):
    from app.workers.collection import claim

    accepted = service.start_run(request(context_account_id="CF-1001"), Caller())
    run, attempt, fence = claim(get_settings(), NOW, "portfolio-test")
    return accepted, Caller(
        principal_id="local-worker",
        actor_kind="worker",
        run_id=run["id"],
        execution_attempt_id=attempt,
        fence=fence,
    )


def test_account_request_cannot_admit_portfolio_task():
    with pytest.raises(ValidationError):
        StartRunRequest(instruction="Chat", task_type="manager_chat", idempotency_key=uuid4())


def test_portfolio_admission_is_durable_and_recoverable_under_concurrent_retries(database_url):
    seed()
    service = ManagerChatService(get_settings(), lambda: NOW)
    payload = request(context_account_id="CF-1001")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.start_run(payload, Caller()), range(4)))
    assert all(result == results[0] for result in results)
    assert service.recover_request(payload.idempotency_key, Caller()) == results[0]
    with connect(database_url) as connection:
        run = connection.execute("SELECT * FROM collection_runs").fetchone()
        assert run["account_id"] is None
        assert run["payload"]["task_type"] == "manager_chat"
        assert run["payload"]["context_account_id"] == "CF-1001"
        assert connection.execute("SELECT count(*) AS n FROM collection_jobs").fetchone()["n"] == 1
        assert connection.execute("SELECT count(*) AS n FROM manager_turns").fetchone()["n"] == 1
    assert service.list_sessions(Caller())["items"][0]["context_account_id"] == "CF-1001"
    assert service.list_sessions(Caller(principal_id="other"))["total"] == 0
    with pytest.raises(DomainError, match="not found"):
        service.recover_request(payload.idempotency_key, Caller(principal_id="other"))


def test_chat_context_is_advisory_and_assignment_fenced(database_url):
    seed()
    service = ManagerChatService(get_settings(), lambda: NOW)
    with connect(database_url) as connection:
        open_review(connection, "CF-1001", "hardship", NOW)
    accepted, caller = active_chat(service)
    context = service.context_for_run(accepted["run_id"], caller)
    assert context["overview"]["total_accounts"] == 8
    assert context["selected_account"]["account"]["id"] == "CF-1001"
    assert context["selected_account"]["open_reviews"][0]["reason"] == "hardship"
    assert context["selected_account"]["policy"]["allowed"] is False
    assert service.search_accounts(accepted["run_id"], query="CF-1002", caller=caller)["total"] == 1
    for forbidden in (
        Caller(),
        replace(caller, fence=caller.fence + 1),
        replace(caller, principal_id="other"),
    ):
        with pytest.raises(DomainError):
            service.overview(accepted["run_id"], forbidden)
    key = uuid4()
    response = service.submit_response(accepted["run_id"], "Enam akun perlu ditinjau.", caller, key)
    assert response["kind"] == "manager_response"
    assert (
        service.submit_response(accepted["run_id"], "Enam akun perlu ditinjau.", caller, key)
        == response
    )
    with pytest.raises(DomainError, match="another payload"):
        service.submit_response(accepted["run_id"], "Different", caller, key)
    proposal = service.propose_task(
        accepted["run_id"], "CF-1001", "collector_brief", "Tinjau hardship", caller, uuid4()
    )
    assert proposal["proposal"] == {
        "account_id": "CF-1001",
        "task_type": "collector_brief",
        "instruction": "Tinjau hardship",
    }
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 1
        assert (
            connection.execute("SELECT count(*) AS n FROM collection_messages").fetchone()["n"] == 0
        )
        assert (
            connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 0
        )
        connection.execute(
            "UPDATE collection_jobs SET lease_until=%s", (NOW - timedelta(seconds=1),)
        )
    with pytest.raises(DomainError):
        service.inspect_account(accepted["run_id"], "CF-1001", caller)


def test_chat_session_cannot_mix_account_scope_and_preserves_latest_context(database_url):
    seed()
    service = ManagerChatService(get_settings(), lambda: NOW)
    first = service.start_run(request(context_account_id="CF-1001"), Caller())
    service.start_run(
        request(manager_session_id=first["manager_session_id"], context_account_id="CF-1002"),
        Caller(),
    )
    assert service.list_sessions(Caller())["items"][0]["context_account_id"] == "CF-1002"
    with pytest.raises(DomainError):
        start_run(
            get_settings(),
            "CF-1001",
            StartRunRequest(
                instruction="Account task",
                idempotency_key=uuid4(),
                manager_session_id=first["manager_session_id"],
            ),
            Caller(),
            NOW,
        )
    with pytest.raises(DomainError):
        service.start_run(
            request(manager_session_id=first["manager_session_id"]), Caller(principal_id="other")
        )
    with pytest.raises(DomainError):
        service.start_run(request(context_account_id="missing"), Caller())
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 2


def test_portfolio_changes_preserve_legacy_account_request_hash(database_url):
    seed()
    payload = StartRunRequest(instruction="Legacy", idempotency_key=uuid4())
    accepted = {"run_id": "previous", "status": "queued"}
    with connect(database_url) as connection:
        record(
            connection,
            "start_run:CF-1001:local-operator",
            str(payload.idempotency_key),
            payload.model_dump(mode="json", exclude={"review_id"}),
            accepted,
        )
    assert start_run(get_settings(), "CF-1001", payload, Caller(), NOW) == accepted


def test_manager_response_bound_matches_runtime_final_result(database_url):
    seed()
    service = ManagerChatService(get_settings(), lambda: NOW)
    accepted, caller = active_chat(service)
    with pytest.raises(DomainError, match="4000"):
        service.submit_response(accepted["run_id"], "x" * 4001, caller, uuid4())


def test_portfolio_chat_http_recovery_is_read_only_and_validates_scope(database_url):
    from fastapi.testclient import TestClient

    from app.main import create_app

    seed()
    payload = request(context_account_id="CF-1001")
    with TestClient(create_app()) as client:
        submitted = client.post("/api/v1/manager/chat/runs", json=payload.model_dump(mode="json"))
        assert submitted.status_code == 202
        accepted = submitted.json()
        snapshot = client.get(f"/api/v1/runs/{accepted['run_id']}")
        assert snapshot.status_code == 200
        assert snapshot.json()["account_id"] is None
        assert snapshot.json()["scope"] == "portfolio"
        sessions = client.get("/api/v1/manager/chat/sessions?limit=1&offset=0").json()
        assert sessions["total"] == 1
        assert sessions["items"][0]["id"] == accepted["manager_session_id"]
        direct = client.get(f"/api/v1/manager/chat/sessions/{accepted['manager_session_id']}")
        assert direct.status_code == 200
        assert direct.json()["id"] == accepted["manager_session_id"]
        assert client.get(f"/api/v1/manager/chat/sessions/{uuid4()}").status_code == 404
        assert (
            client.get(f"/api/v1/manager/chat/requests/{payload.idempotency_key}").json()
            == accepted
        )
        assert client.get(f"/api/v1/manager/chat/requests/{uuid4()}").status_code == 404
        assert client.get("/api/v1/manager/chat/sessions?limit=101").status_code == 422
        assert (
            client.post(
                "/api/v1/accounts/CF-1001/runs", json=payload.model_dump(mode="json")
            ).status_code
            == 422
        )
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 1
        assert (
            connection.execute("SELECT count(*) AS n FROM collection_requests").fetchone()["n"] == 1
        )


def test_direct_session_read_enforces_portfolio_ownership(database_url):
    seed()
    service = ManagerChatService(get_settings(), lambda: NOW)
    owned = service.start_run(request(), Caller())
    session = service.get_session(owned["manager_session_id"], Caller())
    assert session["id"] == owned["manager_session_id"]
    assert session["account_id"] is None
    account = start_run(
        get_settings(),
        "CF-1001",
        StartRunRequest(instruction="Account", idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    for session_id, caller in (
        (owned["manager_session_id"], Caller(principal_id="other")),
        (account["manager_session_id"], Caller()),
        (str(uuid4()), Caller()),
    ):
        with pytest.raises(DomainError, match="not found"):
            service.get_session(session_id, caller)
