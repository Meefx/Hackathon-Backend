from typing import Annotated

from fastapi import APIRouter, Query

from app.config import get_settings
from app.db.connection import connect
from app.modules.audit.schemas import AuditEvent
from app.modules.cases.repository import lock_case
from app.modules.cases.schemas import CaseActionRequest, CaseActionResult, PolicyDecision
from app.modules.collection.service import CollectionService
from app.modules.compliance.schemas import CurrentPolicy
from app.modules.conversations import service as conversations
from app.modules.conversations.schemas import (
    DispatchRequest,
    DispatchResult,
    Message,
    ReceiveMessageRequest,
)
from app.modules.integrations.schemas import AgentRegistry, IntegrationRegistry
from app.modules.integrations.service import agents, integrations
from app.modules.knowledge_context.contracts import KnowledgeSnapshot
from app.modules.manager.schemas import ManagerSession, ManagerTurn
from app.modules.reviews import service as reviews
from app.modules.reviews.schemas import ReviewItem, ReviewResolution, ReviewResolutionResult
from app.modules.runs import repository as runs_repo
from app.modules.runs import service as runs
from app.modules.runs.schemas import (
    CancelRunRequest,
    RecoverRunRequest,
    RunAccepted,
    RunEventPage,
    RunSnapshot,
    StartRunRequest,
)
from app.modules.scheduling.schemas import AutomationStatus
from app.shared.caller import Caller
from app.shared.clock import utcnow
from app.shared.errors import DomainError
from app.shared.schemas import Page

router = APIRouter(prefix="/api/v1", tags=["Collection"])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def page(connection, query, params, limit, offset):
    items = connection.execute(query + " LIMIT %s OFFSET %s", (*params, limit, offset)).fetchall()
    total = connection.execute("SELECT count(*) AS n FROM (" + query + ") page", params).fetchone()[
        "n"
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/agents", response_model=AgentRegistry)
def agent_registry():
    return agents(get_settings())


@router.get("/integrations", response_model=IntegrationRegistry)
def integration_registry():
    return integrations(get_settings())


@router.get("/collection-automation/status", response_model=AutomationStatus)
def automation_status():
    from app.modules.scheduling.service import status

    return status(get_settings(), utcnow())


@router.post("/accounts/{account_id}/runs", response_model=RunAccepted, status_code=202)
def start_run(account_id: str, request: StartRunRequest):
    return runs.start_run(get_settings(), account_id, request, Caller(), utcnow())


@router.get("/runs/{run_id}", response_model=RunSnapshot)
def get_run(run_id: str):
    with connect(get_settings().database_url) as connection:
        return runs_repo.snapshot(connection, run_id)


@router.get("/accounts/{account_id}/runs", response_model=Page[RunSnapshot])
def run_history(
    account_id: str, limit: Limit = 20, offset: Offset = 0, request_key: str | None = None
):
    with connect(get_settings().database_url) as connection:
        lock_case(connection, account_id)
        query = "SELECT * FROM collection_runs WHERE account_id=%s AND caller_id IN (%s,%s)"
        params: tuple[str, ...] = (account_id, Caller().principal_id, "daily-collection-scheduler")
        if request_key:
            query += " AND request_key=%s"
            params += (request_key,)
        result = page(
            connection, query + " ORDER BY created_at DESC,id DESC", params, limit, offset
        )
        result["items"] = [
            RunSnapshot.model_validate(runs_repo.snapshot(connection, r["id"]))
            for r in result["items"]
        ]
        return result


@router.get("/runs/{run_id}/events", response_model=RunEventPage)
def run_events(run_id: str, after_sequence: Annotated[int, Query(ge=0)] = 0, limit: Limit = 100):
    with connect(get_settings().database_url) as connection:
        runs_repo.require_run(connection, run_id)
        items = connection.execute(
            "SELECT * FROM run_events WHERE run_id=%s AND sequence>%s ORDER BY sequence LIMIT %s",
            (run_id, after_sequence, limit + 1),
        ).fetchall()
        return {
            "items": items[:limit],
            "next_sequence": items[min(len(items), limit) - 1]["sequence"]
            if items
            else after_sequence,
            "has_more": len(items) > limit,
        }


@router.post("/runs/{run_id}/cancel", response_model=RunSnapshot)
def cancel_run(run_id: str, request: CancelRunRequest):
    return runs.cancel_run(get_settings(), run_id, request, Caller(), utcnow())


@router.post("/runs/{run_id}/recover", response_model=RunSnapshot)
def recover_run(run_id: str, request: RecoverRunRequest):
    return runs.recover_run(get_settings(), run_id, request, Caller(), utcnow)


@router.get("/accounts/{account_id}/manager-sessions", response_model=Page[ManagerSession])
def manager_sessions(account_id: str, limit: Limit = 20, offset: Offset = 0):
    with connect(get_settings().database_url) as connection:
        lock_case(connection, account_id)
        result = page(
            connection,
            "SELECT * FROM manager_sessions WHERE account_id=%s AND caller_id IN (%s,%s) "
            "ORDER BY created_at DESC,id DESC",
            (account_id, Caller().principal_id, "daily-collection-scheduler"),
            limit,
            offset,
        )
        for session in result["items"]:
            session["origin"] = (
                "scheduled" if session["caller_id"] == "daily-collection-scheduler" else "operator"
            )
        return result


@router.get("/manager-sessions/{session_id}/messages", response_model=Page[ManagerTurn])
def manager_turns(session_id: str, limit: Limit = 20, offset: Offset = 0):
    with connect(get_settings().database_url) as connection:
        if not connection.execute(
            "SELECT id FROM manager_sessions WHERE id=%s AND caller_id IN (%s,%s)",
            (session_id, Caller().principal_id, "daily-collection-scheduler"),
        ).fetchone():
            raise DomainError("not_found", "Manager session not found", 404)
        return page(
            connection,
            "SELECT * FROM manager_turns WHERE session_id=%s ORDER BY turn_index,"
            "CASE role WHEN 'manager' THEN 1 ELSE 0 END",
            (session_id,),
            limit,
            offset,
        )


@router.get("/runs/{run_id}/knowledge", response_model=Page[KnowledgeSnapshot])
def knowledge(run_id: str, limit: Limit = 20, offset: Offset = 0):
    return CollectionService().get_knowledge_history(run_id, limit=limit, offset=offset)


@router.get("/accounts/{account_id}/messages", response_model=Page[Message])
def messages(account_id: str, limit: Limit = 20, offset: Offset = 0):
    CollectionService().get_account(account_id)
    return CollectionService().get_contact_history(account_id, limit=limit, offset=offset)


@router.post("/accounts/{account_id}/messages", response_model=Message, status_code=201)
def receive_message(account_id: str, request: ReceiveMessageRequest):
    return conversations.receive_message(get_settings(), account_id, request, Caller(), utcnow())


@router.post("/messages/{message_id}/simulate-dispatch", response_model=DispatchResult)
def dispatch(message_id: str, request: DispatchRequest):
    return conversations.simulate_dispatch(get_settings(), message_id, request, Caller(), utcnow)


@router.get("/policies/current", response_model=CurrentPolicy)
def current_policy():
    with connect(get_settings().database_url) as connection:
        row = connection.execute("SELECT * FROM collection_policies WHERE active").fetchone()
        if not row:
            raise DomainError("policy_missing", "No active contact policy", 404)
        return {
            "id": row["version"],
            "version": row["version"],
            "timezone": "Asia/Jakarta",
            "contact_start_hour": row["contact_start_hour"],
            "contact_end_hour": row["contact_end_hour"],
            "coverage_until": row["coverage_until"],
            "maximum_daily_messages": row["maximum_daily_messages"],
            "mode": row["mode"],
        }


@router.get("/accounts/{account_id}/contact-policy", response_model=PolicyDecision)
def contact_policy(account_id: str):
    return CollectionService().get_contact_policy(account_id)


@router.post("/accounts/{account_id}/takeover", response_model=CaseActionResult)
def takeover(account_id: str, request: CaseActionRequest):
    return reviews.case_action(get_settings(), account_id, request, Caller(), utcnow(), True)


@router.post("/accounts/{account_id}/resume", response_model=CaseActionResult)
def resume(account_id: str, request: CaseActionRequest):
    return reviews.case_action(get_settings(), account_id, request, Caller(), utcnow())


@router.get("/reviews", response_model=Page[ReviewItem])
def review_list(limit: Limit = 20, offset: Offset = 0, status: str | None = None):
    with connect(get_settings().database_url) as connection:
        query = "SELECT * FROM collection_reviews"
        params: tuple[str, ...] = ()
        if status:
            query += " WHERE status=%s"
            params = (status,)
        return page(connection, query + " ORDER BY created_at DESC,id DESC", params, limit, offset)


@router.get("/reviews/{review_id}", response_model=ReviewItem)
def review_detail(review_id: str):
    with connect(get_settings().database_url) as connection:
        row = connection.execute(
            "SELECT * FROM collection_reviews WHERE id=%s", (review_id,)
        ).fetchone()
        if not row:
            raise DomainError("not_found", "Review not found", 404)
        return row


@router.post("/reviews/{review_id}/resolve", response_model=ReviewResolutionResult)
def resolve_review(review_id: str, request: ReviewResolution):
    return reviews.resolve_review(get_settings(), review_id, request, Caller(), utcnow())


@router.get("/accounts/{account_id}/audit", response_model=Page[AuditEvent])
def audit(account_id: str, limit: Limit = 20, offset: Offset = 0):
    with connect(get_settings().database_url) as connection:
        lock_case(connection, account_id)
        return page(
            connection,
            "SELECT id,account_id,run_id,action,actor_kind,outcome,reason_codes,"
            "correlation_id,created_at,previous_hash,event_hash FROM audit_events "
            "WHERE account_id=%s ORDER BY sequence",
            (account_id,),
            limit,
            offset,
        )
