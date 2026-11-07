"""SDK transport tests against a real loopback HTTP listener and fixture domain port."""
# ruff: noqa: SIM117 - nested SDK contexts preserve explicit transport/session lifetimes

import socket
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import anyio
import anyio.lowlevel
import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import Settings
from app.mcp.server import create_app
from app.shared.errors import DomainError

TOKEN = "fixture-mcp-only-token"
ASSIGNMENT = {
    "account_id": "ACC-001",
    "run_id": "run-1",
    "execution_attempt_id": "attempt-1",
    "fence": 1,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FixtureDomain:
    """Protocol fixture; actual authorization and DB parity are tested separately."""

    def get_account(self, account_id, *, caller):
        if (
            caller.principal_id,
            caller.actor_kind,
            caller.account_id,
            caller.run_id,
            caller.execution_attempt_id,
            caller.fence,
        ) != ("hermes-agent", "agent", "ACC-001", "run-1", "attempt-1", 1):
            raise DomainError("assignment_invalid", status=403)
        return {
            "id": account_id,
            "borrower_name": "Synthetic",
            "loan_reference": "LN-001",
            "outstanding_amount": 100000,
            "due_date": "2026-11-01",
            "assigned_to": "Demo",
            "days_past_due": 6,
            "status": "early",
        }


@contextmanager
def listener(service=None):
    settings = Settings(
        _env_file=None,
        mcp_bearer_token=TOKEN,
        mcp_token_expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    app = create_app(settings, service or FixtureDomain())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


@pytest.mark.anyio
async def test_initialize_discover_and_structured_call_over_real_http():
    with listener() as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.protocolVersion
                    tools = (await session.list_tools()).tools
                    assert {t.name for t in tools} == {
                        "get_account",
                        "get_contact_policy",
                        "get_contact_history",
                        "retrieve_approved_context",
                        "submit_draft_candidate",
                        "submit_run_artifact",
                        "request_human_review",
                    }
                    account_tool = next(t for t in tools if t.name == "get_account")
                    assert (
                        account_tool.outputSchema["properties"]["outstanding_amount"]["type"]
                        == "integer"
                    )
                    assert account_tool.annotations.readOnlyHint
                    result = await session.call_tool("get_account", {"request": ASSIGNMENT})
                    assert not result.isError
                    assert result.structuredContent["id"] == "ACC-001"
                    assert result.structuredContent["outstanding_amount"] == 100000


@pytest.mark.anyio
async def test_cross_target_stale_fence_and_identity_injection_denied():
    with listener() as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for field, value in [
                        ("account_id", "ACC-002"),
                        ("run_id", "other"),
                        ("execution_attempt_id", "old"),
                        ("fence", 2),
                    ]:
                        result = await session.call_tool(
                            "get_account",
                            {
                                "request": {**ASSIGNMENT, field: value},
                            },
                        )
                        assert result.isError
                        assert "assignment_invalid" in result.content[0].text
                    for field in ["role", "approved", "principal_id", "tenant"]:
                        result = await session.call_tool(
                            "get_account",
                            {
                                "request": {**ASSIGNMENT, field: "operator"},
                            },
                        )
                        assert result.isError


@pytest.mark.anyio
async def test_bad_origin_and_bearer_rejected_on_http_boundary():
    with listener() as url:
        async with httpx.AsyncClient() as http:
            assert (await http.post(url)).status_code == 401
            assert (
                await http.post(url, headers={"Authorization": "Bearer invalid"})
            ).status_code == 401
            result = await http.post(
                url,
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "Origin": "https://untrusted.example",
                    "Content-Type": "application/json",
                },
                json={},
            )
            assert result.status_code == 403


@pytest.mark.anyio
async def test_protocol_dependency_failure_is_safe_tool_error(caplog):
    caplog.set_level("INFO", logger="app.mcp.tools.common")

    class UnavailableDomain(FixtureDomain):
        def get_account(self, account_id, *, caller):
            raise ValueError("secret-provider-payload-must-not-leak")

    with listener(UnavailableDomain()) as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("get_account", {"request": ASSIGNMENT})
                    assert result.isError
                    assert "dependency_unavailable" in result.content[0].text
                    assert "secret-provider" not in result.content[0].text
    assert "outcome=dependency_unavailable" in caplog.text
    assert "secret-provider-payload" not in caplog.text


@pytest.mark.anyio
async def test_tool_timeout_waits_for_inflight_sync_cleanup():
    from mcp.server.fastmcp.exceptions import ToolError

    from app.mcp.tools.common import DomainTools
    from app.schemas import Account

    class SlowDomain:
        def get_account(self):
            time.sleep(0.03)
            return {
                "id": "ACC-001",
                "borrower_name": "Synthetic",
                "loan_reference": "LN-001",
                "outstanding_amount": 100000,
                "due_date": "2026-11-01",
                "assigned_to": "Demo",
                "days_past_due": 6,
                "status": "early",
            }

    # A synchronous transaction is not abandoned on disconnect/cancel.
    # Once control returns, an exceeded response budget becomes a timeout.
    # This fixture's sleep is bounded; real psycopg work has its own DB deadlines.
    with pytest.raises(ToolError, match="dependency_timeout"):
        await DomainTools(SlowDomain(), timeout_seconds=0.01).call("get_account", Account)
    await anyio.lowlevel.checkpoint()


@pytest.mark.anyio
async def test_sdk_transport_reports_tool_deadline_after_sync_cleanup(monkeypatch):
    from app.mcp.tools.common import DomainTools

    monkeypatch.setattr(
        "app.mcp.server.DomainTools", lambda service: DomainTools(service, timeout_seconds=0.01)
    )

    class SlowDomain(FixtureDomain):
        def get_account(self, account_id, *, caller):
            time.sleep(0.04)
            return super().get_account(account_id, caller=caller)

    with listener(SlowDomain()) as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("get_account", {"request": ASSIGNMENT})
                    assert result.isError and "dependency_timeout" in result.content[0].text


@pytest.mark.anyio
async def test_sdk_cancelled_read_cleans_up_and_next_call_still_works():
    class SlowDomain(FixtureDomain):
        def get_account(self, account_id, *, caller):
            time.sleep(0.05)
            return super().get_account(account_id, caller=caller)

    with listener(SlowDomain()) as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    with anyio.move_on_after(0.02) as cancelled:
                        await session.call_tool("get_account", {"request": ASSIGNMENT})
                    assert cancelled.cancel_called
                    result = await session.call_tool("get_account", {"request": ASSIGNMENT})
                    assert not result.isError
