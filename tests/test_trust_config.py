"""Configuration must not silently activate trust or weaken signing boundaries."""

import pytest

from app.config import Settings, TrustSettings


def test_integrated_trust_is_opt_in_and_has_no_default_signing_keys(monkeypatch):
    for name in ("TRUST_ENABLED", "TRUST_JWT_SECRET", "TRUST_APPROVAL_SECRET"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_environment()
    assert settings.trust_enabled is False
    assert settings.trust_demo_auth_enabled is False
    with pytest.raises(ValueError, match="at least 32 bytes"):
        settings.trust_settings()


def test_shared_database_and_central_environment_configuration(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/shared")
    monkeypatch.setenv("TRUST_DATABASE_URL", "")
    monkeypatch.setenv("TRUST_JWT_SECRET", "synthetic-test-jwt-" * 3)
    monkeypatch.setenv("TRUST_APPROVAL_SECRET", "synthetic-test-approval-" * 3)
    monkeypatch.setenv("TRUST_DEMO_AUTH_ENABLED", "false")
    monkeypatch.setenv("SIMULATION_MODE", "true")
    settings = TrustSettings.from_environment()
    assert settings.database_url == "postgresql://localhost/shared"
    assert settings.demo_auth_enabled is False
    assert settings.jwt_secret not in repr(settings)
    assert settings.approval_secret not in repr(settings)
    assert settings.jwt_secret not in repr(Settings.from_environment())


def test_independent_signing_keys_are_required():
    with pytest.raises(ValueError, match="must be different"):
        TrustSettings("postgresql://localhost/demo", "same-key-" * 8, "same-key-" * 8)


def test_trust_requires_explicit_database_and_simulation():
    with pytest.raises(ValueError, match="explicit database"):
        TrustSettings("host=localhost", "test-jwt-" * 8, "test-approval-" * 8)
    with pytest.raises(ValueError, match="synthetic simulation only"):
        TrustSettings(
            "postgresql://localhost/demo",
            "test-jwt-" * 8,
            "test-approval-" * 8,
            simulation_mode=False,
        )
