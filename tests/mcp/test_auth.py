from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings


def settings(**values):
    return Settings(
        _env_file=None,
        mcp_bearer_token="fixture-mcp-secret",
        mcp_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        **values,
    )


def test_listener_never_exposes_rest_and_requires_mcp_token():
    from app.mcp.server import create_app

    with TestClient(create_app(settings(), service=object())) as client:
        assert client.get("/api/v1/accounts").status_code == 404
        assert client.post("/mcp").status_code == 401
        assert (
            client.post("/mcp", headers={"Authorization": "Bearer fixture-hermes-key"}).status_code
            == 401
        )


def test_expired_or_unconfigured_mcp_credentials_fail_closed():
    from app.mcp.auth import OpaqueTokenRegistry

    registry = OpaqueTokenRegistry(
        "token", "hermes-agent", datetime.now(UTC) - timedelta(seconds=1)
    )
    assert registry.verify("Bearer token") is None
    assert OpaqueTokenRegistry("", "hermes-agent", None).verify("Bearer ") is None


def test_mcp_opaque_registry_binds_issuer_resource_principal_and_scopes():
    from app.mcp.auth import OpaqueTokenRegistry

    token = OpaqueTokenRegistry("token", "hermes-agent", datetime.now(UTC) + timedelta(hours=1))
    principal = token.verify("Bearer token")
    assert principal.principal_id == "hermes-agent"
    assert principal.issuer == "collectflow-local"
    assert principal.audience == "/mcp"
    assert principal.scopes == frozenset({"collection:read", "collection:write"})
    assert token.verify("Basic token") is None
    assert token.verify("Bearer non-ascii-ä") is None


def test_token_expiry_requires_timezone():
    from app.mcp.auth import OpaqueTokenRegistry

    with pytest.raises(ValueError):
        OpaqueTokenRegistry("token", "hermes-agent", datetime.now())
