from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.schemas import Account, AccountPage, CollectionStatus, Dashboard, Health
from app.store import get_account, initialize, list_accounts, summarize


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize(settings.database_path)
        yield

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Development starter for a lending collections portal. Amounts are whole IDR.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["Accept", "Content-Type"],
    )

    def today():
        return datetime.now(ZoneInfo("Asia/Jakarta")).date()

    @app.get("/health", response_model=Health, tags=["Health"])
    def health():
        return {"status": "ok", "service": settings.app_name}

    @app.get("/api/v1/dashboard", response_model=Dashboard, tags=["Dashboard"])
    def dashboard():
        return summarize(list_accounts(settings.database_path, today()))

    @app.get("/api/v1/accounts", response_model=AccountPage, tags=["Accounts"])
    def accounts(
        search: Annotated[str, Query(max_length=100)] = "",
        status: CollectionStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        """Search borrower or loan reference; ordered by oldest due date, then ID."""
        items = list_accounts(settings.database_path, today())
        needle = search.strip().casefold()
        items = [
            item for item in items
            if (not status or item["status"] == status)
            and (needle in item["borrower_name"].casefold()
                 or needle in item["loan_reference"].casefold())
        ]
        return {"items": items[offset:offset + limit], "total": len(items),
                "limit": limit, "offset": offset}

    @app.get("/api/v1/accounts/{account_id}", response_model=Account, tags=["Accounts"])
    def account_detail(account_id: str):
        account = get_account(settings.database_path, account_id, today())
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        return account

    return app


app = create_app()
