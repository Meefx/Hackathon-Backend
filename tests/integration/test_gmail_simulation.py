"""Opt-in real HTTP verification against the checked-in local Postman simulation.

Run the simulation on port 4859, then set GMAIL_SIMULATION_URL=http://127.0.0.1:4859.
The test transport rewrites only the two fixed Google endpoints to that local server.
"""

import json
import os
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import pytest

from app.config import Settings
from app.integrations.gmail.client import SEND_URL
from app.integrations.gmail.oauth import TOKEN_URL
from app.integrations.gmail.sandbox import send_test
from app.shared.errors import DomainError

SIMULATION_URL = os.environ.get("GMAIL_SIMULATION_URL", "")
pytestmark = pytest.mark.skipif(
    not SIMULATION_URL, reason="Set GMAIL_SIMULATION_URL for local HTTP"
)


class LocalGoogleTransport(httpx.BaseTransport):
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("The simulation must use a plain loopback HTTP origin")
        self.base_url = base_url.rstrip("/")
        self.network = httpx.HTTPTransport(retries=0)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) not in {TOKEN_URL, SEND_URL}:
            raise AssertionError("Only the modeled Google endpoints may use this transport")
        local_url = self.base_url + "/google" + request.url.path
        headers = [(key, value) for key, value in request.headers.multi_items() if key != "host"]
        local_request = httpx.Request(
            request.method,
            local_url,
            headers=headers,
            content=request.content,
            extensions=request.extensions,
        )
        return self.network.handle_request(local_request)

    def close(self) -> None:
        self.network.close()


@pytest.mark.parametrize(
    "scenario,expected_code,accepted",
    [
        ("success", None, 1),
        ("permission_denied", "gmail_permission_denied", 0),
        ("server_error", "gmail_delivery_unknown", 0),
        ("timeout", "gmail_delivery_unknown", 1),
    ],
)
def test_gmail_http_contract_and_durable_no_resend(tmp_path, scenario, expected_code, accepted):
    transport = LocalGoogleTransport(SIMULATION_URL)
    control_url = SIMULATION_URL.rstrip("/") + "/google/__control"
    with httpx.Client(trust_env=False, timeout=3) as control:
        reset = control.post(control_url, json={"scenario": scenario})
        reset.raise_for_status()
        config = Settings(
            _env_file=None,
            gmail_client_id="synthetic-client",
            gmail_client_secret="synthetic-secret",
            gmail_refresh_token="synthetic-refresh",
            gmail_sender_email="sender@example.test",
            gmail_test_recipient="team@example.test",
            gmail_token_path=str(tmp_path / "oauth.json"),
            gmail_timeout_seconds=0.1 if scenario == "timeout" else 2,
        )
        request_id = UUID("00000000-0000-4000-8000-000000000001")
        for _ in range(2):
            if expected_code:
                with pytest.raises(DomainError) as error:
                    send_test(config, request_id, transport=transport)
                assert error.value.code == expected_code
            else:
                assert send_test(config, request_id, transport=transport) == {
                    "id": "synthetic-gmail-message",
                    "status": "submitted",
                }
        observed = control.get(control_url).json()
        assert observed == {
            "scenario": scenario,
            "token_requests": 1,
            "send_requests": 1,
            "accepted": accepted,
        }
    journal = json.loads((tmp_path / "requests" / f"{request_id}.json").read_text())
    expected_status = {
        "success": "submitted",
        "permission_denied": "failed",
        "server_error": "started",
        "timeout": "started",
    }
    assert journal["status"] == expected_status[scenario]
    assert "synthetic-refresh" not in json.dumps(journal)
