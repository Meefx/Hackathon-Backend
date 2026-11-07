"""Main Manager reads approved KB through fenced domain and SDK paths."""
# ruff: noqa: SIM117 - preserve explicit SDK transport/session lifetimes

from dataclasses import replace

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import get_settings
from app.db.connection import connect
from app.modules.knowledge import service as knowledge
from app.modules.manager.chat_service import ManagerChatService
from app.seed import seed
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.workers.collection import claim
from tests.integration.modules.knowledge.test_producer import approved_document
from tests.integration.test_manager_portfolio_chat import NOW, active_chat, request
from tests.mcp.test_manager_service_parity import active_portfolio
from tests.mcp.test_protocol import TOKEN, listener

QUERY = "Borrower-only collection"
REVIEWER_ROLES = ["knowledge", "qa", "compliance"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def publish(connection, actor_factory, **changes):
    return approved_document(connection, actor_factory, NOW, access_roles=REVIEWER_ROLES, **changes)


def test_manager_context_and_search_read_scoped_knowledge_with_run_audit(database_url, trust_actor):
    seed(NOW.date())
    with connect(database_url) as connection:
        own = publish(connection, trust_actor)
        other = publish(connection, trust_actor, principal="ldn", product="spinjam")
        original = connection.execute(
            "SELECT id,status,content_hash FROM trust.knowledge_documents ORDER BY id"
        ).fetchall()
        assert not knowledge.retrieve(connection, trust_actor("personal"), QUERY, NOW)["results"]
    service = ManagerChatService(get_settings(), lambda: NOW)
    accepted, caller = active_chat(service)
    result = service.search_knowledge(accepted["run_id"], QUERY, caller)
    assert result["status"] == "ready"
    assert (result["principal"], result["product"]) == ("cf", "spaylater")
    assert {source["document_id"] for source in result["sources"]} == {own["id"]}
    assert other["id"] not in {source["document_id"] for source in result["sources"]}
    context = service.context_for_run(accepted["run_id"], caller)
    assert {source["document_id"] for source in context["knowledge"]["sources"]} == {own["id"]}
    with connect(database_url) as connection:
        events = connection.execute(
            "SELECT actor_role,payload FROM trust.audit_events "
            "WHERE event_type='manager.knowledge_retrieved' ORDER BY seq"
        ).fetchall()
        assert len(events) == 2
        assert all(event["actor_role"] == "manager" for event in events)
        assert all(event["payload"]["run_id"] == accepted["run_id"] for event in events)
        assert all(event["payload"]["document_ids"] == [own["id"]] for event in events)
        assert all(event["payload"]["chunk_ids"] for event in events)
        assert all("query" not in event["payload"] for event in events)
        assert (
            connection.execute(
                "SELECT id,status,content_hash FROM trust.knowledge_documents ORDER BY id"
            ).fetchall()
            == original
        )
        assert (
            connection.execute("SELECT count(*) AS n FROM collection_messages").fetchone()["n"] == 0
        )
        assert (
            connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 0
        )


def test_manager_knowledge_rejects_invalid_assignment_before_read(database_url, trust_actor):
    seed(NOW.date())
    with connect(database_url) as connection:
        publish(connection, trust_actor)
    service = ManagerChatService(get_settings(), lambda: NOW)
    accepted, caller = active_chat(service)
    for denied in (
        Caller(),
        replace(caller, fence=caller.fence + 1),
        replace(caller, principal_id="other-principal"),
    ):
        with pytest.raises(DomainError) as error:
            service.search_knowledge(accepted["run_id"], QUERY, denied)
        assert error.value.code == "assignment_denied"
    with connect(database_url) as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM trust.audit_events "
                "WHERE event_type='manager.knowledge_retrieved'"
            ).fetchone()["n"]
            == 0
        )


@pytest.mark.parametrize("ambiguous", [False, True])
def test_manager_without_account_requires_unambiguous_portfolio_scope(
    database_url, trust_actor, ambiguous
):
    seed(NOW.date())
    with connect(database_url) as connection:
        own = publish(connection, trust_actor)
        if ambiguous:
            connection.execute(
                "UPDATE collection_authorities SET principal='ldn',product='spinjam' "
                "WHERE account_id='CF-1002'"
            )
    settings = get_settings()
    service = ManagerChatService(settings, lambda: NOW)
    accepted = service.start_run(request(context_account_id=None), Caller())
    run, attempt, fence = claim(settings, NOW, "manager-kb-test")
    caller = Caller(
        principal_id="local-worker",
        actor_kind="worker",
        run_id=run["id"],
        execution_attempt_id=attempt,
        fence=fence,
    )
    result = service.search_knowledge(accepted["run_id"], QUERY, caller)
    if ambiguous:
        assert result["status"] == "unavailable"
        assert result["reason_code"] == "knowledge_scope_required"
        assert not result["sources"]
    else:
        assert result["status"] == "ready"
        assert {source["document_id"] for source in result["sources"]} == {own["id"]}


@pytest.mark.anyio
async def test_manager_knowledge_sdk_returns_citations_and_rejects_scope_injection(
    database_url, trust_actor
):
    manager, assignment = active_portfolio()
    with connect(database_url) as connection:
        own = publish(connection, trust_actor)
        publish(connection, trust_actor, principal="ldn", product="spinjam")
    with listener(manager_service=manager) as url:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    payload = {**assignment, "query": QUERY, "limit": 5}
                    result = await session.call_tool(
                        "search_manager_knowledge", {"request": payload}
                    )
                    assert not result.isError, result
                    assert result.structuredContent["status"] == "ready"
                    sources = result.structuredContent["sources"]
                    assert {source["document_id"] for source in sources} == {own["id"]}
                    assert all(source["citation_id"] for source in sources)
                    for forged in (
                        {**payload, "principal": "ldn", "product": "spinjam"},
                        {**payload, "role": "knowledge"},
                        {**payload, "fence": assignment["fence"] + 1},
                    ):
                        denied = await session.call_tool(
                            "search_manager_knowledge", {"request": forged}
                        )
                        assert denied.isError, denied
    with connect(database_url) as connection:
        event = connection.execute(
            "SELECT actor_role,payload FROM trust.audit_events "
            "WHERE event_type='manager.knowledge_retrieved' ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        assert event["actor_role"] == "manager"
        assert event["payload"]["run_id"] == assignment["run_id"]
        assert (
            connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 0
        )
