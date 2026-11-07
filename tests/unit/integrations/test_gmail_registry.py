from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.modules.integrations.schemas import IntegrationRegistry
from app.modules.integrations.service import integrations


def test_gmail_registry_reports_sandbox_config_separately_from_live_health():
    settings = Settings(
        _env_file=None,
        gmail_client_id="synthetic-client",
        gmail_client_secret="synthetic-secret",
        gmail_refresh_token="synthetic-refresh",
        gmail_sender_email="sender@example.test",
        gmail_test_recipient="team@example.test",
    )
    registry = IntegrationRegistry.model_validate(integrations(settings))
    gmail = next(item for item in registry.items if item.id == "gmail")
    assert gmail.implemented and gmail.configured
    assert gmail.health == "unknown"
    assert gmail.execution_mode == "sandbox"
    assert gmail.capabilities == ["oauth2", "sandbox_send_test"]
    assert gmail.disabled_reason is None
    email = next(item for item in registry.items if item.id == "email")
    assert not email.configured
    serialized = registry.model_dump_json()
    for private in (
        "synthetic-secret",
        "synthetic-refresh",
        "sender@example.test",
        "team@example.test",
    ):
        assert private not in serialized


def test_gmail_registry_returns_unconfigured_when_token_storage_is_invalid(tmp_path):
    path = tmp_path / "oauth.json"
    path.write_text("invalid")
    registry = IntegrationRegistry.model_validate(
        integrations(Settings(_env_file=None, gmail_token_path=str(path)))
    )
    gmail = next(item for item in registry.items if item.id == "gmail")
    assert gmail.implemented and not gmail.configured
    assert gmail.health == "unavailable"
    assert gmail.execution_mode == "unavailable"
    assert gmail.disabled_reason == "gmail_not_configured"


def test_registry_http_contract_includes_only_bounded_gmail_demo_routes(monkeypatch):
    monkeypatch.setattr(
        "app.modules.collection.router.get_settings", lambda: Settings(_env_file=None)
    )
    try:
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/integrations")
            assert response.status_code == 200
            registry = IntegrationRegistry.model_validate(response.json())
            assert any(item.id == "gmail" for item in registry.items)
            paths = client.get("/openapi.json").json()["paths"]
            assert {path for path in paths if "gmail" in path} == {
                "/api/v1/integrations/gmail/collection-demo",
                "/api/v1/integrations/gmail/collection-demo/{demo_id}",
            }
            demo_schema = paths["/api/v1/integrations/gmail/collection-demo"]
            assert set(demo_schema) == {"get", "post"}
    finally:
        get_settings.cache_clear()
