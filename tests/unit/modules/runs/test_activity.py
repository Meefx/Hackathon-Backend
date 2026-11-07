from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.integrations.hermes.client import HermesClient
from app.shared.errors import DomainError


def settings(**changes):
    return Settings(
        _env_file=None, hermes_api_base_url="https://runtime.example",
        hermes_api_key="fixture-secret", **changes,
    )


def run_identity(monkeypatch, **changes):
    from app.modules.runs import activity

    state = {"open": False}

    @contextmanager
    def connect(database_url):
        state["open"] = True
        try:
            yield object()
        finally:
            state["open"] = False

    row = {
        "requested_runtime_mode": "hermes", "actual_runtime_mode": None,
        "external_run_id": "run_fixture", "status": "running", **changes,
    }
    monkeypatch.setattr(activity, "connect", connect)
    monkeypatch.setattr(activity.repo, "require_run", lambda connection, run_id: row)
    return state


def test_activity_reads_identity_then_releases_database_before_network(monkeypatch):
    from app.modules.runs import activity

    state = run_identity(monkeypatch)

    def handler(request):
        assert not state["open"]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content='id: 0\ndata: {"event":"run.completed","run_id":"run_fixture","seq":0}\n\n',
        )

    provider = HermesClient("https://runtime.example", "secret", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(activity, "create_hermes_runtime", lambda config: provider)
    result = activity.get_run_activity(settings(), "application-run", -1, 100)
    assert result.source == "hermes"
    assert result.status == "available"
    assert result.events[0].type == "run.completed"
    assert result.events[0].created_at is None
    assert result.next_sequence == 0


@pytest.mark.parametrize("mode,status", [("simulated", "available"), ("hermes", "not_started")])
def test_activity_reports_simulation_and_queued_runs_without_provider(monkeypatch, mode, status):
    from app.modules.runs import activity

    run_identity(monkeypatch, requested_runtime_mode=mode, external_run_id=None, status="queued")
    result = activity.get_run_activity(settings(), "application-run", 8, 100)
    assert result.source == mode
    assert result.status == status
    assert result.events == []
    assert result.next_sequence == 8
    assert result.reason_code is None


def test_activity_fault_is_explicit_and_does_not_modify_run(monkeypatch):
    from app.modules.runs import activity

    run_identity(monkeypatch)
    provider = HermesClient("https://runtime.example", "secret", transport=httpx.MockTransport(
        lambda request: httpx.Response(404, text="PRIVATE"),
    ))
    monkeypatch.setattr(activity, "create_hermes_runtime", lambda config: provider)
    result = activity.get_run_activity(settings(), "application-run", 4, 100)
    assert result.status == "unavailable"
    assert result.source == "hermes"
    assert result.reason_code == "runtime_run_unknown"
    assert result.events == []
    assert result.next_sequence == 4
    assert "PRIVATE" not in result.model_dump_json()


def test_activity_unconfigured_provider_is_unavailable(monkeypatch):
    from app.modules.runs import activity

    run_identity(monkeypatch)
    result = activity.get_run_activity(Settings(_env_file=None), "application-run", -1, 100)
    assert result.status == "unavailable"
    assert result.reason_code == "runtime_not_configured"


def test_activity_missing_run_keeps_404_even_when_runtime_is_unconfigured(monkeypatch):
    from app.modules.runs import activity

    run_identity(monkeypatch)

    def missing(connection, run_id):
        raise DomainError("not_found", "Run not found", 404)

    monkeypatch.setattr(activity.repo, "require_run", missing)
    with pytest.raises(DomainError) as error:
        activity.get_run_activity(Settings(_env_file=None), "missing", -1, 100)
    assert error.value.status == 404


def test_activity_http_contract_enforces_cursor_and_limit(monkeypatch):
    from app.main import create_app
    from app.modules.runs import activity, router

    run_identity(monkeypatch, requested_runtime_mode="simulated", external_run_id=None)
    monkeypatch.setattr(router, "get_settings", lambda: Settings(_env_file=None))
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/runs/application-run/activity")
        assert response.status_code == 200
        assert response.json() == {
            "source": "simulated", "status": "available", "events": [],
            "next_sequence": -1, "has_more": False, "truncated": False, "reason_code": None,
        }
        for query in ("after_sequence=-2", "limit=0", "limit=101"):
            assert client.get(f"/api/v1/runs/application-run/activity?{query}").status_code == 422

        def missing(connection, run_id):
            raise DomainError("not_found", "Run not found", 404)

        monkeypatch.setattr(activity.repo, "require_run", missing)
        assert client.get("/api/v1/runs/missing/activity").status_code == 404
