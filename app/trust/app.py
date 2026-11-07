"""Trust application wiring, shared by the team API and standalone simulator."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core.clock import Clock, SystemClock
from app.modules.trust.router import PREFIX, create_router
from app.modules.trust.service import Classifier, TrustService
from app.trust.db import pool
from app.trust.errors import TrustError
from app.trust.middleware import BodyLimitMiddleware
from app.trust.screening import MockLayer2
from app.trust.settings import TrustSettings


def configure_trust(
    app: FastAPI,
    settings: TrustSettings,
    clock: Clock | None = None,
    layer2: Classifier | None = None,
    *,
    legacy_routes: bool = False,
) -> None:
    """Register one service and its pool; startup never creates or migrates tables."""
    clock = clock or SystemClock()
    classifier = layer2 or MockLayer2()
    database = pool(settings.database_url)
    service = TrustService(settings, database, clock, classifier)
    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with previous_lifespan(application):
            # psycopg is synchronous; pool lifecycle must not block the event loop.
            try:
                await run_in_threadpool(database.open, wait=True, timeout=5)
                await run_in_threadpool(service.ready)
                yield
            finally:
                await run_in_threadpool(database.close)

    app.router.lifespan_context = lifespan
    app.state.trust_clock = clock
    app.state.trust_service = service
    app.add_middleware(BodyLimitMiddleware)

    @app.exception_handler(TrustError)
    async def trust_error(request: Request, exc: TrustError) -> JSONResponse:
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
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        if not legacy_routes and not request.url.path.startswith(PREFIX + "/"):
            return await request_validation_exception_handler(request, exc)
        # FastAPI defaults may echo rejected PII. Return field locations only.
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
    async def database_error(request: Request, exc: psycopg.Error) -> JSONResponse:
        return JSONResponse(status_code=503, content={"code": "DATABASE_UNAVAILABLE"})

    @app.middleware("http")
    async def boundaries(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        size = request.headers.get("content-length", "0")
        if not size.isdigit() or int(size) > 300000:
            return JSONResponse(status_code=413, content={"code": "REQUEST_TOO_LARGE"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    router = create_router(service)
    app.include_router(router, prefix=PREFIX)
    if legacy_routes:
        # Temporary standalone compatibility only; never mounted in the team app.
        app.include_router(router, include_in_schema=False)


def create_trust_app(
    settings: TrustSettings | None = None,
    clock: Clock | None = None,
    layer2: Classifier | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Graceline Trust API",
        version="0.2.0",
        description="Synthetic-only evidence control. Mock classifier; no legal clearance.",
    )
    configure_trust(
        app, settings or TrustSettings.from_environment(), clock, layer2, legacy_routes=True
    )
    return app
