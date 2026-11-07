"""Opt-in API/worker collection tests against the local Google HTTP stand-in."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.integrations.gmail.client import GmailClient
from tests.integration.test_gmail_simulation import SIMULATION_URL, LocalGoogleTransport

pytestmark = pytest.mark.skipif(
    not SIMULATION_URL, reason="Set GMAIL_SIMULATION_URL for local HTTP"
)
NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)
PATH = "/api/v1/integrations/gmail/collection-demo"


@pytest.mark.parametrize(
    "scenario,expected_status,reason,accepted",
    [
        ("success", "submitted", None, 1),
        ("permission_denied", "failed", "gmail_permission_denied", 0),
        ("server_error", "delivery_unknown", "gmail_delivery_unknown", 0),
        ("timeout", "delivery_unknown", "gmail_delivery_unknown", 1),
    ],
)
def test_email_demo_api_worker_and_http_no_resend(
    database_url, monkeypatch, tmp_path, scenario, expected_status, reason, accepted
):
    from app.main import create_app
    from app.modules.email_demo import router
    from app.seed import seed
    from app.workers.collection import process_cycle

    seed(NOW.date())
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        scheduler_enabled=False,
        knowledge_mode="demo_fixture",
        demo_fixture_enabled=True,
        gmail_client_id="synthetic-client",
        gmail_client_secret="synthetic-secret",
        gmail_refresh_token="synthetic-refresh",
        gmail_sender_email="sender@example.test",
        gmail_test_recipient="team@example.test",
        gmail_token_path=str(tmp_path / "oauth.json"),
        gmail_timeout_seconds=0.1 if scenario == "timeout" else 2,
    )
    monkeypatch.setattr(router, "get_settings", lambda: settings)
    monkeypatch.setattr(router, "utcnow", lambda: NOW)
    monkeypatch.setattr(
        "app.integrations.channels.email.GmailClient",
        lambda config: GmailClient(config, transport=LocalGoogleTransport(SIMULATION_URL)),
    )
    control_url = SIMULATION_URL.rstrip("/") + "/google/__control"
    with httpx.Client(trust_env=False, timeout=3) as control:
        control.post(control_url, json={"scenario": scenario}).raise_for_status()
        with TestClient(create_app()) as client:
            initial = client.get(PATH)
            assert initial.status_code == 200
            assert initial.json()["latest"] is None
            key = str(uuid4())
            response = client.post(PATH, json={"idempotency_key": key})
            assert response.status_code == 202, response.text
            demo = response.json()
            assert demo["runtime_mode"] == "simulated"
            assert process_cycle(settings, now=NOW)
            finished = client.get(f"{PATH}/{demo['id']}").json()
            assert finished["status"] == expected_status, finished
            if reason:
                assert reason in finished["reason_codes"]
            else:
                assert finished["provider_message_id"] == "synthetic-gmail-message"
            # Reading, worker restart and retrying the same logical request do not resend.
            for _ in range(2):
                client.get(f"{PATH}/{demo['id']}").raise_for_status()
                process_cycle(settings, now=NOW)
            replay = client.post(PATH, json={"idempotency_key": key})
            assert replay.status_code == 202
            assert replay.json()["id"] == demo["id"]
            assert replay.json()["run_id"] == demo["run_id"]
            process_cycle(settings, now=NOW)
            observed = control.get(control_url).json()
            assert observed == {
                "scenario": scenario,
                "token_requests": 1,
                "send_requests": 1,
                "accepted": accepted,
            }
            if scenario == "success":
                another = client.post(PATH, json={"idempotency_key": str(uuid4())})
                assert another.status_code == 202
                assert another.json()["account_id"] != demo["account_id"]
                assert process_cycle(settings, now=NOW)
                second = client.get(f"{PATH}/{another.json()['id']}").json()
                assert second["status"] == "submitted", second
                assert second["provider_message_id"] != finished["provider_message_id"]
                assert control.get(control_url).json()["accepted"] == 2
