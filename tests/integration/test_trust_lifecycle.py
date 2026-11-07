"""Optional KMS availability must not own the portfolio application's lifecycle."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolClosed, PoolTimeout

from app.config import get_settings
from app.main import create_app
from app.modules.trust.service import TrustService
from app.seed import seed
from app.trust.errors import TrustError


def configure_optional_trust(monkeypatch, database_url):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TRUST_DATABASE_URL", database_url)
    monkeypatch.setenv("TRUST_ENABLED", "true")
    monkeypatch.setenv("TRUST_DEMO_AUTH_ENABLED", "true")
    monkeypatch.setenv("TRUST_JWT_SECRET", "j" * 48)
    monkeypatch.setenv("TRUST_APPROVAL_SECRET", "a" * 48)
    monkeypatch.setenv("DEMO_FIXTURE_ENABLED", "true")
    get_settings.cache_clear()


def test_optional_trust_unready_preserves_health_portfolio_and_inbound(monkeypatch, database_url):
    configure_optional_trust(monkeypatch, database_url)
    seed()

    def unready(self):
        raise TrustError("DATABASE_NOT_READY", 503)

    monkeypatch.setattr(TrustService, "ready", unready)
    app = create_app()
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 200
            assert client.get("/api/v1/accounts").json()["total"] == 8
            inbound = client.post(
                "/api/v1/accounts/CF-1001/messages",
                json={
                    "text": "I need help with a repayment difficulty.",
                    "demo_scenario": "hardship",
                    "idempotency_key": str(uuid4()),
                },
            )
            assert inbound.status_code == 201
            assert inbound.json()["status"] == "received"
            readiness = client.get("/api/v1/trust/ready")
            assert readiness.status_code == 503
            assert readiness.json()["code"] == "DATABASE_NOT_READY"
        assert app.state.trust_service.database.closed
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("failure", [PoolClosed, PoolTimeout])
def test_optional_trust_pool_errors_are_sanitized(monkeypatch, database_url, failure):
    configure_optional_trust(monkeypatch, database_url)

    def unavailable(*args, **kwargs):
        raise failure("Synthetic internal pool failure must not be exposed")

    app = create_app()
    monkeypatch.setattr(app.state.trust_service, "documents", unavailable)
    try:
        with TestClient(app) as client:
            token = client.post("/api/v1/trust/auth/demo-login", json={"role": "personal"}).json()[
                "access_token"
            ]
            response = client.get(
                "/api/v1/trust/knowledge/documents", headers={"Authorization": "Bearer " + token}
            )
            assert response.status_code == 503
            assert response.json() == {"code": "DATABASE_UNAVAILABLE"}
            assert client.get("/health").status_code == 200
        assert app.state.trust_service.database.closed
    finally:
        get_settings.cache_clear()


def test_optional_trust_pool_open_failure_does_not_report_ready(monkeypatch, database_url):
    configure_optional_trust(monkeypatch, database_url)
    app = create_app()

    def unavailable(*args, **kwargs):
        raise PoolClosed("Synthetic pool cannot open")

    monkeypatch.setattr(app.state.trust_service.database, "open", unavailable)
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/api/v1/accounts").status_code == 200
            readiness = client.get("/api/v1/trust/ready")
            assert readiness.status_code == 503
            assert readiness.json()["code"] == "DATABASE_UNAVAILABLE"
        assert app.state.trust_service.database.closed
    finally:
        get_settings.cache_clear()
