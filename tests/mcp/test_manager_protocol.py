"""Portfolio tools preserve authenticated assignment boundaries over SDK HTTP."""
# ruff: noqa: SIM117 - explicit SDK transport/session scopes

from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp.exceptions import ToolError

from app.mcp.auth import OpaqueTokenRegistry, Principal, current_principal
from app.mcp.schemas.manager import PortfolioAssignment
from app.mcp.tools.common import portfolio_caller
from app.shared.errors import DomainError
from tests.mcp.test_protocol import TOKEN, FixtureDomain, listener

PORTFOLIO_ASSIGNMENT = {
    "run_id": "portfolio-1",
    "execution_attempt_id": "portfolio-attempt-1",
    "fence": 1,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FixturePortfolio:
    def validate(self, run_id, caller):
        if (
            caller.principal_id,
            caller.actor_kind,
            caller.account_id,
            caller.run_id,
            run_id,
            caller.execution_attempt_id,
            caller.fence,
        ) != (
            "hermes-agent", "agent", None, "portfolio-1", "portfolio-1",
            "portfolio-attempt-1", 1,
        ):
            raise DomainError("assignment_denied", status=403)

    def overview(self, run_id, *, caller):
        self.validate(run_id, caller)
        return {
            "total_accounts": 1, "total_outstanding": 100000,
            "overdue_accounts": 1, "overdue_amount": 100000,
            "buckets": [{"status": "early", "count": 1, "outstanding_amount": 100000}],
        }

    def search_accounts(self, run_id, *, query, status, limit, offset, caller):
        self.validate(run_id, caller)
        assert (query, status, limit, offset) == ("Synthetic", "early", 5, 0)
        return {
            "items": [FixtureDomain().get_account("ACC-001", caller=replace(
                caller, account_id="ACC-001", run_id="run-1",
                execution_attempt_id="attempt-1",
            ))],
            "total": 1, "limit": limit, "offset": offset,
        }

    def inspect_account(self, run_id, account_id, *, caller):
        self.validate(run_id, caller)
        assert account_id == "ACC-001"
        return {
            "account": self.search_accounts(
                run_id, query="Synthetic", status="early", limit=5, offset=0, caller=caller,
            )["items"][0],
            "policy": {
                "account_id": account_id, "case_version": 1, "allowed": True,
                "reason_codes": [], "policy_version": "1", "contact_state": "active",
                "evaluated_at": "2026-11-07T03:00:00Z",
            },
            "recent_messages": [{
                "direction": "inbound", "text": "Synthetic question",
                "created_at": "2026-11-07T03:00:00Z",
            }],
            "open_reviews": [],
        }

    def submit_response(self, run_id, text, *, caller, idempotency_key):
        self.validate(run_id, caller)
        assert str(idempotency_key)
        return {
            "id": "response-1", "run_id": run_id, "kind": "manager_response", "text": text,
            "created_at": "2026-11-07T03:00:00Z",
        }

    def propose_task(self, run_id, account_id, task_type, instruction, *, caller, idempotency_key):
        self.validate(run_id, caller)
        assert str(idempotency_key)
        return {
            "id": "proposal-1", "run_id": run_id, "kind": "manager_task_proposal",
            "text": instruction, "created_at": "2026-11-07T03:00:00Z",
            "proposal": {"account_id": account_id, "task_type": task_type,
                         "instruction": instruction},
        }


@asynccontextmanager
async def portfolio_session(service=None):
    with listener(manager_service=service or FixturePortfolio()) as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


@pytest.mark.anyio
async def test_manager_tools_discover_and_return_typed_portfolio_and_advisory_artifacts():
    async with portfolio_session() as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        for name in ("get_portfolio_overview", "search_portfolio_accounts", "inspect_portfolio_account"):
            assert tools[name].annotations.readOnlyHint
        assert not tools["propose_account_task"].annotations.readOnlyHint
        assert tools["propose_account_task"].annotations.idempotentHint
        requests = [
            ("get_portfolio_overview", {}, "total_accounts", 1),
            ("search_portfolio_accounts", {"query": "Synthetic", "status": "early", "limit": 5},
             "total", 1),
            ("inspect_portfolio_account", {"account_id": "ACC-001"}, "open_reviews", []),
            ("submit_manager_response", {"text": "Portfolio needs review", "idempotency_key": str(uuid4())},
             "kind", "manager_response"),
            ("propose_account_task", {"account_id": "ACC-001", "task_type": "collector_brief",
             "instruction": "Review this account", "idempotency_key": str(uuid4())},
             "proposal", {"account_id": "ACC-001", "task_type": "collector_brief",
                          "instruction": "Review this account"}),
        ]
        for name, payload, field, expected in requests:
            result = await session.call_tool(name, {"request": {**PORTFOLIO_ASSIGNMENT, **payload}})
            assert not result.isError, result
            assert result.structuredContent[field] == expected


@pytest.mark.anyio
async def test_portfolio_tools_reject_stale_assignment_and_authority_injection():
    async with portfolio_session() as session:
        for field, value in [("run_id", "other"), ("execution_attempt_id", "old"), ("fence", 2)]:
            result = await session.call_tool(
                "get_portfolio_overview", {"request": {**PORTFOLIO_ASSIGNMENT, field: value}},
            )
            assert result.isError and "assignment_denied" in result.content[0].text
        for field in ["account_id", "principal_id", "role", "approved", "tenant"]:
            result = await session.call_tool(
                "get_portfolio_overview", {"request": {**PORTFOLIO_ASSIGNMENT, field: "injected"}},
            )
            assert result.isError
        result = await session.call_tool("search_portfolio_accounts", {
            "request": {**PORTFOLIO_ASSIGNMENT, "limit": 101},
        })
        assert result.isError
        result = await session.call_tool("propose_account_task", {"request": {
            **PORTFOLIO_ASSIGNMENT, "account_id": "ACC-001", "task_type": "dispatch",
            "instruction": "Dispatch", "idempotency_key": str(uuid4()),
        }})
        assert result.isError


@pytest.mark.anyio
async def test_manager_write_scope_checked_at_execution(monkeypatch):
    verify = OpaqueTokenRegistry.verify

    def read_only(self, authorization):
        principal = verify(self, authorization)
        return replace(principal, scopes=frozenset({"collection:read"})) if principal else None

    monkeypatch.setattr(OpaqueTokenRegistry, "verify", read_only)
    async with portfolio_session() as session:
        result = await session.call_tool("get_portfolio_overview", {"request": PORTFOLIO_ASSIGNMENT})
        assert not result.isError
        for name, payload in [
            ("submit_manager_response", {"text": "Response"}),
            ("propose_account_task", {"account_id": "ACC-001", "task_type": "collector_brief",
                                     "instruction": "Review"}),
        ]:
            result = await session.call_tool(name, {"request": {
                **PORTFOLIO_ASSIGNMENT, **payload, "idempotency_key": str(uuid4()),
            }})
            assert result.isError and "permission_denied" in result.content[0].text


@pytest.mark.anyio
async def test_portfolio_dependency_failure_redacts_details():
    class BrokenPortfolio(FixturePortfolio):
        def overview(self, run_id, *, caller):
            raise RuntimeError("private database details")

    async with portfolio_session(BrokenPortfolio()) as session:
        result = await session.call_tool("get_portfolio_overview", {"request": PORTFOLIO_ASSIGNMENT})
        assert result.isError and "dependency_unavailable" in result.content[0].text
        assert "private database" not in result.content[0].text


@pytest.mark.anyio
async def test_manager_response_rejects_text_exceeding_final_result_limit():
    async with portfolio_session() as session:
        result = await session.call_tool("submit_manager_response", {"request": {
            **PORTFOLIO_ASSIGNMENT, "text": "a" * 4001, "idempotency_key": str(uuid4()),
        }})
        assert result.isError


@pytest.mark.parametrize("principal,write", [
    (None, False),
    (Principal("agent", audience="/other"), False),
    (Principal("agent", scopes=frozenset({"collection:write"})), False),
    (Principal("agent", scopes=frozenset({"collection:read"})), True),
])
def test_portfolio_caller_requires_authenticated_resource_and_operation_scope(principal, write):
    token = current_principal.set(principal)
    try:
        with pytest.raises(ToolError, match="permission_denied"):
            portfolio_caller(PortfolioAssignment(**PORTFOLIO_ASSIGNMENT), write=write)
    finally:
        current_principal.reset(token)
