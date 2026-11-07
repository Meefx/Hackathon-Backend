"""Synchronous HTTP adapter for the trust service; no SQL or policy decisions."""

from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.modules.trust import schemas
from app.modules.trust.service import TrustService
from app.trust.errors import TrustError
from app.trust.security import Actor, decode_token

PREFIX = "/api/v1/trust"


def create_router(service: TrustService) -> APIRouter:
    router = APIRouter(
        tags=["Trust simulation"],
        responses={
            status: {"model": model}
            for status, model in {
                401: schemas.ActionProblem,
                403: schemas.ActionProblem,
                404: schemas.ActionProblem,
                409: schemas.ActionProblem,
                413: schemas.CodeProblem,
                422: schemas.ValidationProblem | schemas.ActionProblem,
                500: schemas.ActionProblem,
                503: schemas.CodeProblem | schemas.ActionProblem,
            }.items()
        },
    )
    bearer = HTTPBearer(auto_error=False)

    def actor(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Actor:
        if credentials is None:
            raise TrustError("AUTHENTICATION_REQUIRED", 401)
        return decode_token(
            credentials.credentials, service.settings.jwt_secret, service.clock.now()
        )

    def key(value: Annotated[UUID, Header(alias="Idempotency-Key")]) -> UUID:
        return value

    Who = Annotated[Actor, Depends(actor)]
    Key = Annotated[UUID, Depends(key)]
    from app.modules.knowledge.router import create_router as knowledge_router

    router.include_router(knowledge_router(service, actor, key))

    @router.get("/health", response_model=schemas.TrustHealth)
    def health() -> dict[str, Any]:
        return {"status": "ok", "simulation_mode": True}

    @router.get("/ready", response_model=schemas.TrustReadiness)
    def ready() -> dict[str, Any]:
        return service.ready()

    @router.post("/auth/demo-login", response_model=schemas.DemoToken)
    def login(data: schemas.Login) -> dict[str, Any]:
        return service.demo_login(data.role, data.principal)

    @router.post("/cases", response_model=schemas.CaseResult)
    def cases(data: schemas.CaseInput, who: Who, mutation_key: Key) -> dict[str, Any]:
        return service.create_case(who, mutation_key, data.model_dump(mode="json"))

    @router.post(
        "/cases/{case_id}/outreach",
        response_model=schemas.OutreachResult,
        response_model_exclude_none=True,
    )
    def outreach(
        case_id: UUID, data: schemas.Attempt, who: Who, mutation_key: Key
    ) -> dict[str, Any]:
        return service.outreach(who, mutation_key, case_id, data.template_id)

    @router.post("/cases/{case_id}/conversations", response_model=schemas.ConversationResult)
    def conversation(
        case_id: UUID, data: schemas.Conversation, who: Who, mutation_key: Key
    ) -> dict[str, Any]:
        return service.receive_conversation(
            who, mutation_key, case_id, data.model_dump(mode="json")
        )

    @router.get("/cases/{case_id}/conversations", response_model=list[schemas.ConversationItem])
    def conversations(case_id: UUID, who: Who) -> list[dict]:
        return service.conversations(who, case_id)

    @router.get("/audit/events", response_model=list[schemas.AuditEvent])
    def events(who: Who) -> list[dict]:
        return service.events(who)

    @router.get(
        "/audit/verify", response_model=schemas.AuditVerification, response_model_exclude_none=False
    )
    def verify(who: Who) -> dict[str, Any]:
        return service.verify(who)

    @router.get("/console", response_class=HTMLResponse, include_in_schema=False)
    def console() -> str:
        return (Path(__file__).resolve().parents[2] / "trust" / "console.html").read_text()

    return router
