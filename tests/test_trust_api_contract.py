"""Versioned trust routes share safety services with the standalone demo."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.core.clock import FrozenClock
from app.main import create_app
from app.trust.app import create_trust_app
from app.trust.demo import run_scenario
from app.trust.settings import TrustSettings


def trust_settings(dsn: str) -> TrustSettings:
    return TrustSettings(dsn, "j" * 48, "a" * 48, demo_auth_enabled=True)


def test_versioned_routes_run_complete_evidence_revocation_loop(trust_dsn, trust_now):
    with TestClient(create_trust_app(trust_settings(trust_dsn), FrozenClock(trust_now))) as client:
        result = run_scenario(client)
        assert result["retrieved_before_retirement"] is True
        assert result["retrieved_after_retirement"] is False
        assert result["first_attempt"] == "DELIVERED"
        assert result["attempt_after_retirement"] == "NOT_SENT"
        assert result["audit"]["valid"] is True
        specification = client.get("/openapi.json").json()
        assert all(path.startswith("/api/v1/trust/") for path in specification["paths"])
        for name, path in specification["paths"].items():
            for operation in path.values():
                content = operation["responses"]["200"]["content"]
                if name == "/api/v1/trust/knowledge/sources/{source_id}/download":
                    assert content == {
                        "application/pdf": {"schema": {"type": "string", "format": "binary"}}
                    }
                else:
                    assert content["application/json"]["schema"]


def test_main_registers_only_versioned_trust_routes(monkeypatch, trust_dsn):
    monkeypatch.setenv("DATABASE_URL", trust_dsn)
    monkeypatch.setenv("TRUST_DATABASE_URL", trust_dsn)
    monkeypatch.setenv("TRUST_ENABLED", "true")
    monkeypatch.setenv("TRUST_DEMO_AUTH_ENABLED", "true")
    monkeypatch.setenv("TRUST_JWT_SECRET", "j" * 48)
    monkeypatch.setenv("TRUST_APPROVAL_SECRET", "a" * 48)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            assert client.get("/health").json()["status"] == "ok"
            assert client.get("/ready").status_code == 200
            assert client.get("/api/v1/accounts").json()["items"] == []
            assert client.get("/knowledge/documents").status_code == 404
            assert client.get("/api/v1/trust/knowledge/documents").status_code == 401
            token = client.post("/api/v1/trust/auth/demo-login", json={"role": "operator"}).json()[
                "access_token"
            ]
            headers = {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())}
            response = client.post(
                "/api/v1/trust/cases", json={"is_synthetic": True}, headers=headers
            )
            assert response.status_code == 409
            assert response.json()["code"] == "legacy_case_mapping_required"
            assert (
                client.post(
                    "/api/v1/trust/cases", json={"is_synthetic": True}, headers=headers
                ).json()
                == response.json()
            )
            preflight = client.options(
                "/api/v1/trust/cases",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Authorization,Idempotency-Key",
                },
            )
            assert preflight.status_code == 200
    finally:
        get_settings.cache_clear()


def test_main_trust_is_disabled_without_explicit_configuration(monkeypatch):
    monkeypatch.setenv("TRUST_ENABLED", "false")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            assert (
                client.post("/api/v1/trust/auth/demo-login", json={"role": "operator"}).status_code
                == 404
            )
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "suffix", ["limit=0", "limit=101", "offset=-1", "offset=9223372036854775808"]
)
@pytest.mark.parametrize("path", ["knowledge/documents", "knowledge/graph", "templates"])
def test_bounded_http_list_validation(trust_dsn, trust_now, suffix, path):
    with TestClient(create_trust_app(trust_settings(trust_dsn), FrozenClock(trust_now))) as client:
        token = client.post("/api/v1/trust/auth/demo-login", json={"role": "personal"}).json()[
            "access_token"
        ]
        response = client.get(
            f"/api/v1/trust/{path}?{suffix}", headers={"Authorization": "Bearer " + token}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "INVALID_REQUEST"


def test_search_uses_time_after_waiting_for_authority_lock(
    monkeypatch, trust_dsn, trust_now, trust_actor
):
    from datetime import timedelta

    from app.modules.trust import repository
    from app.trust.db import connection
    from tests.test_knowledge import approved_document

    with connection(trust_dsn) as conn:
        approved_document(
            conn,
            trust_actor,
            trust_now,
            effective_until=(trust_now + timedelta(seconds=1)).isoformat(),
        )
    clock = FrozenClock(trust_now)
    lock = repository.lock_audit

    def lock_crosses_expiry(conn):
        lock(conn)
        # Simulate time elapsed while the authority lock was unavailable.
        clock.instant = trust_now + timedelta(seconds=2)

    monkeypatch.setattr(repository, "lock_audit", lock_crosses_expiry)
    with TestClient(create_trust_app(trust_settings(trust_dsn), clock)) as client:
        token = client.post("/api/v1/trust/auth/demo-login", json={"role": "personal"}).json()[
            "access_token"
        ]
        response = client.post(
            "/api/v1/trust/knowledge/search",
            json={"query": "borrower"},
            headers={"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 200
        assert response.json()["results"] == []


def test_unmigrated_trust_startup_fails_without_creating_tables(empty_database_url):
    import psycopg

    from app.trust.errors import TrustError

    with (
        pytest.raises((TrustError, psycopg.Error)),
        TestClient(create_trust_app(trust_settings(empty_database_url))),
    ):
        pytest.fail("Trust must refuse an unmigrated database")
    with psycopg.connect(empty_database_url) as conn:
        assert conn.execute("SELECT to_regclass('trust.knowledge_documents')").fetchone() == (None,)
        assert conn.execute("SELECT to_regclass('alembic_version')").fetchone() == (None,)
