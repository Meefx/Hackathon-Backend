import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.integrations.channels.waha.client import WahaClient
from app.shared.errors import DomainError


def local_settings(**overrides):
    return Settings(
        _env_file=None,
        waha_base_url="http://127.0.0.1:3001",
        waha_api_key="test-api-key",
        **overrides,
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://127.0.0.1.example.com:3001",
        "http://user:password@localhost:3001",
        "http://localhost:3001/path",
        "http://localhost:3001?secret=value",
        "http://localhost:3001\n",
        "http://local\thost:3001",
        "file:///tmp/waha",
    ],
)
def test_sandbox_configuration_rejects_nonlocal_or_credentialed_urls(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, waha_base_url=url)


def test_status_uses_api_key_and_returns_only_sanitized_session_state():
    def provider(request):
        assert request.method == "GET"
        assert str(request.url) == "http://127.0.0.1:3001/api/sessions/default"
        assert request.headers["x-api-key"] == "test-api-key"
        return httpx.Response(
            200,
            json={
                "name": "default",
                "status": "SCAN_QR_CODE",
                "config": {"webhooks": []},
                "me": {"id": "6280000000000@c.us", "pushName": "Private test name"},
                "engine": {"engine": "WEBJS"},
            },
        )

    client = WahaClient(local_settings(), transport=httpx.MockTransport(provider))
    assert client.session_status() == {"session": "default", "status": "SCAN_QR_CODE"}


def test_missing_configuration_fails_before_any_network_request():
    with pytest.raises(DomainError) as error:
        WahaClient(Settings(_env_file=None)).session_status()
    assert error.value.code == "waha_not_configured"


@pytest.mark.parametrize("state", ["PASSKEY_REQUIRED", "PASSKEY_CONFIRMATION_REQUIRED"])
def test_passkey_pairing_state_is_reported_without_claiming_connection(state):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"name": "default", "status": state})
    )
    client = WahaClient(local_settings(), transport=transport)
    assert client.session_status() == {"session": "default", "status": state}


@pytest.mark.parametrize("status,code", [(401, "waha_auth_rejected"), (503, "waha_unavailable")])
def test_provider_errors_do_not_expose_response_secrets(status, code):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, text="private-token-and-session-data")
    )
    with pytest.raises(DomainError) as error:
        WahaClient(local_settings(), transport=transport).session_status()
    assert error.value.code == code
    assert "private-token" not in str(error.value)


def test_redirect_is_rejected_without_forwarding_the_api_key():
    calls = []

    def provider(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://example.com"})

    with pytest.raises(DomainError) as error:
        WahaClient(local_settings(), transport=httpx.MockTransport(provider)).session_status()
    assert error.value.code == "waha_redirect_rejected"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [{"name": "other", "status": "WORKING"}, {"name": "default", "status": "unknown"}, []],
)
def test_malformed_session_response_cannot_report_a_working_session(payload):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(DomainError) as error:
        WahaClient(local_settings(), transport=transport).session_status()
    assert error.value.code == "waha_response_invalid"


def test_qr_image_uses_documented_endpoint_and_validates_image_response():
    png = b"\x89PNG\r\n\x1a\n" + b"synthetic-test-image"

    def provider(request):
        assert request.url.path == "/api/default/auth/qr"
        assert request.url.params["format"] == "image"
        return httpx.Response(200, content=png, headers={"Content-Type": "image/png"})

    assert WahaClient(local_settings(), transport=httpx.MockTransport(provider)).qr_image() == png


def test_oversized_provider_response_is_rejected():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * 1_048_577)
    )
    with pytest.raises(DomainError) as error:
        WahaClient(local_settings(), transport=transport).session_status()
    assert error.value.code == "waha_response_invalid"


def test_test_send_uses_only_configured_recipient_and_fixed_synthetic_text():
    calls = []

    def provider(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"name": "default", "status": "WORKING"})
        assert request.url.path == "/api/sendText"
        payload = json.loads(request.content)
        assert payload["session"] == "default"
        assert payload["chatId"] == "6280000000000@c.us"
        assert "synthetic" in payload["text"].lower()
        assert set(payload) == {"session", "chatId", "text"}
        return httpx.Response(201, json={"id": "synthetic-message-id", "ack": 0})

    client = WahaClient(
        local_settings(waha_test_recipient="6280000000000"),
        transport=httpx.MockTransport(provider),
    )
    assert client.send_test_message() == {"id": "synthetic-message-id", "status": "submitted"}
    assert [call.method for call in calls] == ["GET", "POST"]


@pytest.mark.parametrize("recipient", ["", "08123456789", "6280000000000@g.us", "../default"])
def test_invalid_or_missing_test_recipient_never_reaches_provider(recipient):
    calls = []
    client = WahaClient(
        local_settings(waha_test_recipient=recipient),
        transport=httpx.MockTransport(lambda request: calls.append(request)),
    )
    with pytest.raises(DomainError) as error:
        client.send_test_message()
    assert error.value.code == "waha_test_recipient_invalid"
    assert calls == []


def test_unpaired_session_cannot_send_a_test_message():
    calls = []

    def provider(request):
        calls.append(request.method)
        return httpx.Response(200, json={"name": "default", "status": "SCAN_QR_CODE"})

    client = WahaClient(
        local_settings(waha_test_recipient="6280000000000"),
        transport=httpx.MockTransport(provider),
    )
    with pytest.raises(DomainError) as error:
        client.send_test_message()
    assert error.value.code == "waha_session_not_ready"
    assert calls == ["GET"]


@pytest.mark.parametrize("failure", ["timeout", "malformed", "server_error"])
def test_ambiguous_send_is_reported_without_retry(failure):
    calls = []

    def provider(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"name": "default", "status": "WORKING"})
        if failure == "timeout":
            raise httpx.ReadTimeout("private-network-details")
        if failure == "malformed":
            return httpx.Response(200, json={"ack": 0})
        return httpx.Response(500, text="private-provider-error")

    client = WahaClient(
        local_settings(waha_test_recipient="6280000000000"),
        transport=httpx.MockTransport(provider),
    )
    with pytest.raises(DomainError) as error:
        client.send_test_message()
    assert error.value.code == "waha_delivery_unknown"
    assert calls == ["GET", "POST"]


def test_cli_reads_only_explicit_local_file_and_sanitizes_output(tmp_path, capsys):
    from app.integrations.channels.waha.__main__ import main

    config = tmp_path / ".env.waha.local"
    config.write_text("WAHA_BASE_URL=http://127.0.0.1:1\nWAHA_API_KEY=private-token\n")
    assert main(["--env-file", str(config), "status"]) == 1
    output = capsys.readouterr()
    assert "waha_unavailable" in output.err
    assert "private-token" not in output.err


def test_cli_writes_qr_to_private_file(tmp_path, monkeypatch, capsys):
    from app.integrations.channels.waha.__main__ import main

    config = tmp_path / ".env.waha.local"
    config.write_text("WAHA_BASE_URL=http://127.0.0.1:3001\nWAHA_API_KEY=test-key\n")
    monkeypatch.setattr(WahaClient, "qr_image", lambda client: b"synthetic-qr")
    output = tmp_path / "qr.png"
    assert main(["--env-file", str(config), "qr", "--output", str(output)]) == 0
    assert output.read_bytes() == b"synthetic-qr"
    assert output.stat().st_mode & 0o777 == 0o600
    assert str(Path(output)) in capsys.readouterr().out
