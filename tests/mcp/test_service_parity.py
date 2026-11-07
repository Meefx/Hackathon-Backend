"""Real PostgreSQL assignment/gate behavior through the real SDK HTTP transport."""

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
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run
from app.seed import seed
from app.shared.caller import Caller
from app.shared.clock import utcnow
from app.workers.collection import claim
from tests.mcp.test_protocol import TOKEN, listener

# ruff: noqa: SIM117 - explicit SDK transport/session scopes


@pytest.fixture
def anyio_backend():
    return "asyncio"


def assigned_service():
    seed()
    settings = get_settings()
    now = utcnow()
    request = StartRunRequest(
        instruction="Siapkan pengingat sintetis", runtime_mode="hermes", idempotency_key=uuid4()
    )
    accepted = start_run(settings, "CF-1001", request, Caller(), now)
    _, attempt, fence = claim(settings, now, "protocol-test-worker")
    assignment = {
        "account_id": "CF-1001",
        "run_id": accepted["run_id"],
        "execution_attempt_id": attempt,
        "fence": fence,
    }
    # Domain business clock is deterministic; execution lease uses the real worker clock.
    service = CollectionService(settings, clock=lambda: datetime(2026, 11, 7, 3, tzinfo=UTC))
    return service, assignment


@pytest.mark.anyio
async def test_real_service_context_candidate_gate_and_replay(database_url):
    service, assignment = assigned_service()
    with listener(service) as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    account = await session.call_tool("get_account", {"request": assignment})
                    assert not account.isError
                    assert account.structuredContent["id"] == "CF-1001"
                    context = await session.call_tool(
                        "retrieve_approved_context",
                        {
                            "request": {
                                **assignment,
                                "query": "SOP pengingat",
                                "purpose": "collection_draft",
                                "deadline_at": (utcnow() + timedelta(seconds=20)).isoformat(),
                            }
                        },
                    )
                    assert not context.isError, context
                    citation = context.structuredContent["sources"][0]["citation_id"]
                    candidate = {
                        "account_id": assignment["account_id"],
                        "run_id": assignment["run_id"],
                        "execution_attempt_id": assignment["execution_attempt_id"],
                        "text_with_placeholders": "Halo {borrower_name}, mari diskusikan pinjaman Anda.",
                        "citation_ids": [citation],
                        "idempotency_key": str(uuid4()),
                    }
                    args = {"request": {**assignment, "candidate": candidate}}
                    first = await session.call_tool("submit_draft_candidate", args)
                    second = await session.call_tool("submit_draft_candidate", args)
                    assert not first.isError and not second.isError, first
                    assert first.structuredContent == second.structuredContent
                    # A live-runtime draft is quarantined while live classifier is unavailable.
                    assert first.structuredContent["status"] == "blocked"
                    with connect(database_url) as connection:
                        assert (
                            connection.execute(
                                "SELECT count(*) AS n FROM collection_messages"
                            ).fetchone()["n"]
                            == 1
                        )
                        assert (
                            connection.execute(
                                "SELECT count(*) AS n FROM message_approvals"
                            ).fetchone()["n"]
                            == 0
                        )
                    invalid = await session.call_tool(
                        "get_account",
                        {
                            "request": {
                                **assignment,
                                "account_id": "CF-1002",
                            }
                        },
                    )
                    assert invalid.isError and "assignment_denied" in invalid.content[0].text


@pytest.mark.anyio
async def test_review_write_replay_survives_its_own_pause(database_url):
    service, assignment = assigned_service()
    with listener(service) as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    args = {
                        "request": {
                            **assignment,
                            "reason": "hardship",
                            "note": "Perlu manusia",
                            "idempotency_key": str(uuid4()),
                        }
                    }
                    first = await session.call_tool("request_human_review", args)
                    replay = await session.call_tool("request_human_review", args)
                    assert not first.isError, first
                    assert not replay.isError, replay
                    assert first.structuredContent["id"] == replay.structuredContent["id"]
                    with connect(database_url) as connection:
                        assert (
                            connection.execute(
                                "SELECT count(*) AS n FROM collection_reviews"
                            ).fetchone()["n"]
                            == 1
                        )


@pytest.mark.anyio
async def test_agent_history_redacts_operational_identifiers(database_url):
    service, assignment = assigned_service()
    raw = "Alex Morgan LN-1001 alice@example.test 081234567890 meminta informasi."
    receive_message(
        get_settings(),
        "CF-1001",
        ReceiveMessageRequest(text=raw, idempotency_key=uuid4()),
        Caller(),
        datetime(2026, 11, 7, 3, tzinfo=UTC),
    )
    assert service.get_contact_history("CF-1001")["items"][0]["text"] == raw
    with listener(service) as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "get_contact_history",
                        {"request": {**assignment, "limit": 20}},
                    )
                    assert not result.isError
                    context = result.structuredContent["items"][0]["text"]
                    for identifier in (
                        "Alex Morgan",
                        "LN-1001",
                        "alice@example.test",
                        "081234567890",
                    ):
                        assert identifier not in context
                    assert "[IDENTIFIER]" in context
