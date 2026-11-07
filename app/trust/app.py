"""Opt-in trust API. Can run standalone alongside the team's existing API."""

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.core.clock import SystemClock
from app.trust import knowledge, runtime
from app.trust.audit import append_event, canonical, verify_chain
from app.trust.db import initialize, pool
from app.trust.errors import TrustError
from app.trust.middleware import BodyLimitMiddleware
from app.trust.pipeline import execute
from app.trust.screening import MockLayer2
from app.trust.security import Actor, decode_token, issue_token, require
from app.trust.settings import TrustSettings


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(StrictModel):
    role: Literal[
        "knowledge",
        "qa",
        "personal",
        "process",
        "manager",
        "operator",
        "officer",
        "compliance",
        "admin",
    ]
    principal: Literal["cf", "ldn"] = "cf"


class Document(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=64000)
    source_uri: str = Field(max_length=2000)
    source_class: Literal["synthetic", "primary", "guidance"] = "synthetic"
    source_version: str = Field(min_length=1, max_length=100)
    source_locator: str = Field(min_length=1, max_length=300)
    review_status: Literal["synthetic", "verified", "review_required"] = "synthetic"
    effective_from: datetime
    effective_until: datetime | None = None
    criticality: Literal["standard", "critical"] = "standard"
    access_roles: list[str] = Field(min_length=1, max_length=10)


class Template(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=400)
    document_ids: list[UUID] = Field(min_length=1, max_length=20)
    evidence_weights: dict[str, float] = Field(default_factory=dict)


class Note(StrictModel):
    note: str = Field(min_length=3, max_length=1000)


class Retrieval(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class CaseInput(StrictModel):
    is_synthetic: Literal[True]
    timezone: str | None = "Asia/Jakarta"
    borrower_ref: UUID | None = None


class Attempt(StrictModel):
    template_id: UUID


class Conversation(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
    is_synthetic: Literal[True]


def create_trust_app(settings=None, clock=None, layer2=None):
    settings = settings or TrustSettings.from_environment()
    clock = clock or SystemClock()
    classifier = layer2 or MockLayer2()
    database = pool(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        initialize(settings.database_url)
        database.open(wait=True, timeout=5)
        yield
        database.close()

    app = FastAPI(
        title="Graceline Trust API",
        version="0.1.0",
        lifespan=lifespan,
        description="Synthetic-only evidence control. Mock classifier; no legal clearance.",
    )
    app.state.trust_clock = clock
    app.add_middleware(BodyLimitMiddleware)
    bearer = HTTPBearer(auto_error=False)

    @app.exception_handler(TrustError)
    async def trust_error(request, exc):
        return JSONResponse(
            status_code=exc.status,
            content={
                "type": "about:blank",
                "title": "Action refused",
                "status": exc.status,
                "code": exc.code,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's default includes input values, which may contain rejected PII.
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_REQUEST",
                "errors": [
                    {"location": list(error["loc"]), "type": error["type"]}
                    for error in exc.errors()
                ],
            },
        )

    @app.exception_handler(psycopg.Error)
    async def database_error(request, exc):
        return JSONResponse(status_code=503, content={"code": "DATABASE_UNAVAILABLE"})

    @app.middleware("http")
    async def boundaries(request, call_next):
        size = request.headers.get("content-length", "0")
        if not size.isdigit() or int(size) > 300000:
            return JSONResponse(status_code=413, content={"code": "REQUEST_TOO_LARGE"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def actor(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None:
            raise TrustError("AUTHENTICATION_REQUIRED", 401)
        return decode_token(credentials.credentials, settings.jwt_secret, clock.now())

    def key(value: Annotated[UUID, Header(alias="Idempotency-Key")]):
        return value

    def write(who, mutation_key, route, payload, action):
        import hashlib

        capabilities = {
            "ingest": "knowledge:ingest",
            "template": "template:create",
            "case": "case:create",
        }
        if route.startswith("document/"):
            capability = "knowledge:" + route.rsplit("/", 1)[-1]
        elif route.startswith("template/"):
            capability = "template:" + route.rsplit("/", 1)[-1]
        elif route.startswith("case/"):
            capability = "attempt:create" if route.endswith("/outreach") else "conversation:write"
        else:
            capability = capabilities.get(route, "denied")
        require(who, capability)
        request_hash = hashlib.sha256(canonical(jsonable_encoder(payload)).encode()).hexdigest()
        with database.connection() as conn:
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL lock_timeout='3s'")
            conn.execute("SELECT pg_advisory_xact_lock(71107002)")
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (who.principal + who.product + str(mutation_key) + route,),
            )
            previous = conn.execute(
                """SELECT * FROM trust.request_records
                WHERE principal=%s AND product=%s AND key=%s AND route=%s""",
                (who.principal, who.product, mutation_key, route),
            ).fetchone()
            if previous:
                if previous["request_hash"] != request_hash or previous["actor_id"] != who.subject:
                    raise TrustError("IDEMPOTENCY_MISMATCH")
                return previous["response"]
            result = jsonable_encoder(action(conn))
            conn.execute(
                """INSERT INTO trust.request_records VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    who.principal,
                    who.product,
                    mutation_key,
                    route,
                    who.subject,
                    request_hash,
                    Jsonb(result),
                ),
            )
            return result

    Who = Annotated[Actor, Depends(actor)]
    Key = Annotated[UUID, Depends(key)]

    @app.get("/health")
    def health():
        return {"status": "ok", "simulation_mode": True}

    @app.get("/ready")
    def ready():
        with database.connection() as conn:
            vector = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname='vector'"
            ).fetchone()
        return {
            "ready": bool(vector),
            "simulation_mode": True,
            "classifier_profile": classifier.profile,
            "semantic_embeddings": False,
        }

    @app.post("/auth/demo-login")
    def login(data: Login):
        if not settings.demo_auth_enabled:
            raise TrustError("DEMO_AUTH_DISABLED", 404)
        who = Actor(
            f"demo-{data.role}",
            data.role,
            data.principal,
            "spaylater" if data.principal == "cf" else "spinjam",
        )
        return {
            "access_token": issue_token(who, settings.jwt_secret, clock.now()),
            "token_type": "bearer",
            "simulation_mode": True,
            "warning": "Demo identity selection only. No production authentication.",
        }

    @app.post("/knowledge/documents")
    def ingest(data: Document, who: Who, mutation_key: Key):
        return write(
            who,
            mutation_key,
            "ingest",
            data.model_dump(mode="json"),
            lambda conn: knowledge.ingest(conn, who, data.model_dump(mode="json"), clock.now()),
        )

    @app.get("/knowledge/documents")
    def documents(who: Who):
        with database.connection() as conn:
            return knowledge.list_documents(conn, who)

    @app.post("/knowledge/documents/{document_id}/qa")
    def qa_document(document_id: UUID, who: Who, mutation_key: Key):
        return write(
            who,
            mutation_key,
            f"document/{document_id}/qa",
            {},
            lambda conn: knowledge.qa_review(conn, who, str(document_id), clock.now()),
        )

    @app.post("/knowledge/documents/{document_id}/approve")
    def approve_document(document_id: UUID, data: Note, who: Who, mutation_key: Key):
        return write(
            who,
            mutation_key,
            f"document/{document_id}/approve",
            data.model_dump(),
            lambda conn: knowledge.approve(conn, who, str(document_id), data.note, clock.now()),
        )

    @app.post("/knowledge/documents/{document_id}/retire")
    def retire_document(document_id: UUID, data: Note, who: Who, mutation_key: Key):
        return write(
            who,
            mutation_key,
            f"document/{document_id}/retire",
            data.model_dump(),
            lambda conn: knowledge.retire(conn, who, str(document_id), data.note, clock.now()),
        )

    @app.post("/knowledge/search")
    def search(data: Retrieval, who: Who, mutation_key: Key):
        # Search must re-evaluate current permissions/status, even for a reused key.
        with database.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(71107002)")
            return knowledge.retrieve(conn, who, data.query, clock.now(), data.limit)

    @app.get("/knowledge/graph")
    def graph(who: Who):
        with database.connection() as conn:
            return knowledge.dependency_graph(conn, who)

    @app.post("/templates")
    def template(data: Template, who: Who, mutation_key: Key):
        payload = data.model_dump(mode="json")
        return write(
            who,
            mutation_key,
            "template",
            payload,
            lambda conn: knowledge.create_template(conn, who, payload, clock.now()),
        )

    @app.get("/templates")
    def templates(who: Who):
        with database.connection() as conn:
            return knowledge.list_templates(conn, who)

    @app.post("/templates/{template_id}/qa")
    def qa_template(template_id: UUID, who: Who, mutation_key: Key):
        return write(
            who,
            mutation_key,
            f"template/{template_id}/qa",
            {},
            lambda conn: knowledge.qa_template(conn, who, str(template_id), clock.now()),
        )

    @app.post("/templates/{template_id}/approve")
    def approve_template(template_id: UUID, data: Note, who: Who, mutation_key: Key):
        return write(
            who,
            mutation_key,
            f"template/{template_id}/approve",
            data.model_dump(),
            lambda conn: knowledge.approve_template(
                conn, who, str(template_id), data.note, clock.now()
            ),
        )

    @app.post("/cases")
    def cases(data: CaseInput, who: Who, mutation_key: Key):
        payload = data.model_dump(mode="json")
        return write(
            who,
            mutation_key,
            "case",
            payload,
            lambda conn: runtime.create_case(conn, who, payload, clock.now()),
        )

    @app.post("/cases/{case_id}/outreach")
    def outreach(case_id: UUID, data: Attempt, who: Who, mutation_key: Key):
        return write(
            who,
            mutation_key,
            f"case/{case_id}/outreach",
            data.model_dump(mode="json"),
            lambda conn: execute(
                conn,
                who,
                str(case_id),
                str(data.template_id),
                str(mutation_key),
                settings.approval_secret,
                clock,
                classifier,
            ),
        )

    @app.post("/cases/{case_id}/conversations")
    def conversation(case_id: UUID, data: Conversation, who: Who, mutation_key: Key):
        return write(
            who,
            mutation_key,
            f"case/{case_id}/conversation",
            data.model_dump(),
            lambda conn: runtime.receive_conversation(
                conn, who, str(case_id), data.text, clock.now()
            ),
        )

    @app.get("/cases/{case_id}/conversations")
    def conversations(case_id: UUID, who: Who):
        with database.connection() as conn:
            return runtime.read_conversations(conn, who, str(case_id))

    @app.get("/audit/events")
    def events(who: Who):
        require(who, "audit:read")
        with database.connection() as conn:
            return conn.execute(
                """SELECT * FROM trust.audit_events WHERE principal=%s AND product=%s
                ORDER BY seq DESC LIMIT 100""",
                (who.principal, who.product),
            ).fetchall()

    @app.get("/audit/verify")
    def verify(who: Who):
        require(who, "audit:verify")
        with database.connection() as conn:
            result = verify_chain(conn)
            append_event(
                conn,
                who,
                "audit.verified",
                {"valid": result["valid"], "checked": result["checked"]},
                clock.now(),
            )
            return result

    @app.get("/console", response_class=HTMLResponse, include_in_schema=False)
    def console():
        return (Path(__file__).parent / "console.html").read_text()

    return app
