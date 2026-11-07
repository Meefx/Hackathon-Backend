"""Real portfolio assignment, advisory persistence, and redaction through SDK HTTP."""
# ruff: noqa: SIM117 - explicit SDK transport/session scopes

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import get_settings
from app.db.connection import connect
from app.modules.collection.service import CollectionService
from app.modules.conversations.schemas import ReceiveMessageRequest
from app.modules.conversations.service import receive_message
from app.modules.manager.chat_schemas import ManagerChatRequest
from app.modules.manager.chat_service import ManagerChatService
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run
from app.seed import seed
from app.shared.caller import Caller
from app.workers.collection import claim
from tests.mcp.test_protocol import TOKEN, listener

NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def active_portfolio():
    seed()
    settings = get_settings()
    service = ManagerChatService(settings, lambda: NOW)
    accepted = service.start_run(ManagerChatRequest(
        instruction="Analyze portfolio", runtime_mode="hermes", idempotency_key=uuid4(),
        context_account_id="CF-1001",
    ), Caller())
    _, attempt, fence = claim(settings, NOW, "manager-mcp-test")
    return service, {
        "run_id": accepted["run_id"], "execution_attempt_id": attempt, "fence": fence,
    }


@pytest.mark.anyio
async def test_portfolio_sdk_reads_multiple_accounts_and_persists_advice_without_dispatch(database_url):
    manager, assignment = active_portfolio()
    settings = get_settings()
    collection = CollectionService(settings, lambda: NOW)
    receive_message(settings, "CF-1001", ReceiveMessageRequest(
        text="Alex Morgan LN-1001 alice@example.test 081234567890 meminta informasi.",
        idempotency_key=uuid4(),
    ), Caller(), NOW)
    with listener(collection, manager_service=manager) as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("get_portfolio_overview", {
                        "request": assignment,
                    })
                    assert not result.isError, result
                    assert result.structuredContent["total_accounts"] == 8
                    result = await session.call_tool("search_portfolio_accounts", {
                        "request": {**assignment, "query": "CF-1002", "limit": 1},
                    })
                    assert not result.isError, result
                    assert result.structuredContent["items"][0]["id"] == "CF-1002"
                    assert result.structuredContent["total"] == 1
                    result = await session.call_tool("inspect_portfolio_account", {
                        "request": {**assignment, "account_id": "CF-1001"},
                    })
                    assert not result.isError, result
                    message = result.structuredContent["recent_messages"][0]["text"]
                    for identifier in ("Alex Morgan", "LN-1001", "alice@example.test", "081234567890"):
                        assert identifier not in message
                    assert "[IDENTIFIER]" in message
                    for name, payload, kind in [
                        ("submit_manager_response", {"text": "Eight accounts need review"},
                         "manager_response"),
                        ("propose_account_task", {"account_id": "CF-1002", "task_type": "collector_brief",
                         "instruction": "Review this account"}, "manager_task_proposal"),
                    ]:
                        request = {**assignment, **payload, "idempotency_key": str(uuid4())}
                        first = await session.call_tool(name, {"request": request})
                        again = await session.call_tool(name, {"request": request})
                        assert not first.isError and not again.isError, first
                        assert first.structuredContent == again.structuredContent
                        assert first.structuredContent["kind"] == kind
                    denied = await session.call_tool("get_account", {"request": {
                        **assignment, "account_id": "CF-1002",
                    }})
                    assert denied.isError and "assignment_denied" in denied.content[0].text
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 1
        assert connection.execute("SELECT count(*) AS n FROM run_artifacts").fetchone()["n"] == 2
        assert connection.execute("SELECT count(*) AS n FROM collection_messages").fetchone()["n"] == 1
        assert connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 0


@pytest.mark.anyio
async def test_portfolio_sdk_rejects_wrong_scope_stale_lease_and_cancelled_run(database_url):
    manager, assignment = active_portfolio()
    settings = get_settings()
    accepted = start_run(settings, "CF-1001", StartRunRequest(
        instruction="Account task", idempotency_key=uuid4(),
    ), Caller(), NOW)
    _, account_attempt, account_fence = claim(settings, NOW, "account-mcp-test")
    with listener(manager_service=manager) as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for request in [
                        {**assignment, "fence": assignment["fence"] + 1},
                        {**assignment, "execution_attempt_id": str(uuid4())},
                        {"run_id": accepted["run_id"], "execution_attempt_id": account_attempt,
                         "fence": account_fence},
                    ]:
                        result = await session.call_tool("get_portfolio_overview", {"request": request})
                        assert result.isError and "assignment_denied" in result.content[0].text
                    with connect(database_url) as connection:
                        connection.execute("UPDATE collection_jobs SET lease_until=%s WHERE run_id=%s",
                                           (NOW - timedelta(seconds=1), assignment["run_id"]))
                    result = await session.call_tool("inspect_portfolio_account", {"request": {
                        **assignment, "account_id": "CF-1002",
                    }})
                    assert result.isError and "assignment_denied" in result.content[0].text
                    with connect(database_url) as connection:
                        connection.execute("UPDATE collection_jobs SET lease_until=%s WHERE run_id=%s",
                                           (NOW + timedelta(minutes=1), assignment["run_id"]))
                        connection.execute("UPDATE collection_runs SET cancellation_requested_at=%s WHERE id=%s",
                                           (NOW, assignment["run_id"]))
                    result = await session.call_tool("submit_manager_response", {"request": {
                        **assignment, "text": "Late response", "idempotency_key": str(uuid4()),
                    }})
                    assert result.isError and "assignment_denied" in result.content[0].text
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM run_artifacts").fetchone()["n"] == 0
