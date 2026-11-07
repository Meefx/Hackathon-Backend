from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.connection import connect
from app.integrations.agents.contracts import RuntimeStatus
from app.main import create_app
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run
from app.seed import seed
from app.shared.caller import Caller


def test_reconciliation_polls_existing_external_run_without_resubmit(database_url, monkeypatch):
    seed()
    now = datetime(2026, 11, 7, 3, tzinfo=UTC)
    run = start_run(
        get_settings(),
        "CF-1001",
        StartRunRequest(instruction="Follow up", runtime_mode="hermes", idempotency_key=uuid4()),
        Caller(),
        now,
    )
    with connect(database_url) as connection:
        connection.execute(
            "UPDATE collection_runs SET status='interrupted',external_run_id='existing' "
            "WHERE id=%s",
            (run["run_id"],),
        )
    calls = []

    class Runtime:
        def get_status(self, external_run_id):
            calls.append(external_run_id)
            return RuntimeStatus(
                external_run_id=external_run_id, actual_runtime_mode="hermes", status="cancelled"
            )

    monkeypatch.setattr(
        "app.integrations.hermes.runtime.create_hermes_runtime", lambda _: Runtime()
    )
    with TestClient(create_app()) as client:
        version = client.get(f"/api/v1/runs/{run['run_id']}").json()["version"]
        payload = {
            "action": "reconcile",
            "expected_version": version,
            "idempotency_key": str(uuid4()),
        }
        response = client.post(f"/api/v1/runs/{run['run_id']}/recover", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "cancelled"
        assert (
            client.post(f"/api/v1/runs/{run['run_id']}/recover", json=payload).json()
            == response.json()
        )
    assert calls == ["existing"]
