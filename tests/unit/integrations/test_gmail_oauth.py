import base64
import hashlib
import json
import os
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.config import Settings
from app.integrations.gmail.oauth import (
    GMAIL_SEND_SCOPE,
    GoogleOAuthClient,
    read_refresh_token,
)
from app.shared.errors import DomainError


def configuration(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        **{
            "gmail_client_id": "test-client.apps.googleusercontent.com",
            "gmail_client_secret": "test-client-secret",
            "gmail_refresh_token": "test-refresh-token",
            "gmail_token_path": str(tmp_path / "gmail" / "oauth.json"),
            **overrides,
        },
    )


def test_refresh_uses_fixed_google_endpoint_and_minimal_form(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": "test-access", "token_type": "Bearer", "scope": GMAIL_SEND_SCOPE},
        )

    client = GoogleOAuthClient(configuration(tmp_path), transport=httpx.MockTransport(respond))
    assert client.access_token() == "test-access"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://oauth2.googleapis.com/token"
    assert parse_qs(requests[0].content.decode()) == {
        "client_id": ["test-client.apps.googleusercontent.com"],
        "client_secret": ["test-client-secret"],
        "refresh_token": ["test-refresh-token"],
        "grant_type": ["refresh_token"],
    }


@pytest.mark.parametrize("field", ["gmail_client_id", "gmail_client_secret", "gmail_refresh_token"])
def test_missing_refresh_configuration_fails_before_network(tmp_path, field):
    def respond(request):
        pytest.fail("Missing credentials must not contact Google")

    client = GoogleOAuthClient(
        configuration(tmp_path, **{field: ""}), transport=httpx.MockTransport(respond)
    )
    with pytest.raises(DomainError) as error:
        client.access_token()
    assert error.value.code == "gmail_not_configured"


@pytest.mark.parametrize(
    "status,body,code",
    [
        (
            400,
            {"error": "invalid_grant", "error_description": "secret-details"},
            "gmail_auth_rejected",
        ),
        (401, {"error": "invalid_client"}, "gmail_auth_rejected"),
        (429, {"error": "rate_limited"}, "gmail_unavailable"),
        (503, {"error": "backend_error"}, "gmail_unavailable"),
        (302, {}, "gmail_response_invalid"),
        (200, [], "gmail_response_invalid"),
        (200, {"access_token": "", "token_type": "Bearer"}, "gmail_response_invalid"),
        (
            200,
            {"access_token": "x\r\nAuthorization: secret", "token_type": "Bearer"},
            "gmail_response_invalid",
        ),
        (200, {"access_token": "test-access", "token_type": "Basic"}, "gmail_response_invalid"),
        (
            200,
            {"access_token": "test-access", "token_type": "Bearer", "scope": "openid"},
            "gmail_scope_invalid",
        ),
    ],
)
def test_token_failures_are_sanitized_and_never_retried(tmp_path, status, body, code):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=body, headers={"Location": "https://example.invalid"})

    with pytest.raises(DomainError) as error:
        GoogleOAuthClient(
            configuration(tmp_path), transport=httpx.MockTransport(respond)
        ).access_token()
    assert error.value.code == code
    assert "secret-details" not in str(error.value)
    assert len(requests) == 1


def test_token_response_is_bounded(tmp_path):
    with pytest.raises(DomainError) as error:
        GoogleOAuthClient(
            configuration(tmp_path),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * 65537)
            ),
        ).access_token()
    assert error.value.code == "gmail_response_invalid"


def test_token_timeout_is_sanitized(tmp_path):
    def respond(request):
        raise httpx.ReadTimeout("secret-provider-data", request=request)

    with pytest.raises(DomainError) as error:
        GoogleOAuthClient(
            configuration(tmp_path), transport=httpx.MockTransport(respond)
        ).access_token()
    assert error.value.code == "gmail_unavailable"
    assert "secret-provider-data" not in str(error.value)


def test_environment_refresh_token_takes_precedence_without_reading_file(tmp_path):
    settings = configuration(tmp_path)
    Path(settings.gmail_token_path).parent.mkdir()
    Path(settings.gmail_token_path).write_text("invalid")
    assert read_refresh_token(settings) == "test-refresh-token"


def test_saved_token_is_tied_to_oauth_client_and_read_privately(tmp_path):
    settings = configuration(tmp_path, gmail_refresh_token="")
    token_file = Path(settings.gmail_token_path)
    token_file.parent.mkdir(mode=0o700)
    token_file.write_text(
        json.dumps({"client_id": settings.gmail_client_id, "refresh_token": "saved"})
    )
    token_file.chmod(0o600)
    assert read_refresh_token(settings) == "saved"
    changed = configuration(tmp_path, gmail_refresh_token="", gmail_client_id="other-client")
    assert read_refresh_token(changed) == ""


def test_missing_saved_token_is_unconfigured(tmp_path):
    assert read_refresh_token(configuration(tmp_path, gmail_refresh_token="")) == ""


@pytest.mark.parametrize("contents", ["not-json", "[]", '{"refresh_token": 123}'])
def test_malformed_saved_token_is_sanitized(tmp_path, contents):
    settings = configuration(tmp_path, gmail_refresh_token="")
    token_file = Path(settings.gmail_token_path)
    token_file.parent.mkdir(mode=0o700)
    token_file.write_text(contents)
    token_file.chmod(0o600)
    with pytest.raises(DomainError) as error:
        read_refresh_token(settings)
    assert error.value.code == "gmail_token_file_invalid"


def test_saved_token_symlink_is_rejected(tmp_path):
    settings = configuration(tmp_path, gmail_refresh_token="")
    target = tmp_path / "target.json"
    target.write_text('{"refresh_token":"secret"}')
    Path(settings.gmail_token_path).parent.mkdir()
    Path(settings.gmail_token_path).symlink_to(target)
    with pytest.raises(DomainError) as error:
        read_refresh_token(settings)
    assert error.value.code == "gmail_token_file_invalid"


def test_saved_token_parent_symlink_is_rejected(tmp_path):
    settings = configuration(tmp_path, gmail_refresh_token="")
    target = tmp_path / "token-storage"
    target.mkdir(mode=0o700)
    (target / "oauth.json").write_text(
        json.dumps({"client_id": settings.gmail_client_id, "refresh_token": "secret"})
    )
    (target / "oauth.json").chmod(0o600)
    Path(settings.gmail_token_path).parent.symlink_to(target)
    with pytest.raises(DomainError) as error:
        read_refresh_token(settings)
    assert error.value.code == "gmail_token_file_invalid"


def test_saved_token_fifo_is_rejected_without_blocking(tmp_path):
    settings = configuration(tmp_path, gmail_refresh_token="")
    token_file = Path(settings.gmail_token_path)
    token_file.parent.mkdir(mode=0o700)
    os.mkfifo(token_file, 0o600)
    with pytest.raises(DomainError) as error:
        read_refresh_token(settings)
    assert error.value.code == "gmail_token_file_invalid"


def test_public_saved_token_permissions_are_rejected(tmp_path):
    settings = configuration(tmp_path, gmail_refresh_token="")
    token_file = Path(settings.gmail_token_path)
    token_file.parent.mkdir(mode=0o700)
    token_file.write_text(
        json.dumps({"client_id": settings.gmail_client_id, "refresh_token": "secret"})
    )
    token_file.chmod(0o644)
    with pytest.raises(DomainError) as error:
        read_refresh_token(settings)
    assert error.value.code == "gmail_token_file_invalid"


def test_authorize_uses_state_pkce_and_private_refresh_storage(tmp_path, monkeypatch, capsys):
    settings = configuration(tmp_path, gmail_refresh_token="")
    browser_urls = []
    exchanges = []

    class FakeServer:
        def __init__(self, address, handler):
            assert address == ("127.0.0.1", settings.gmail_oauth_port)
            self.callback_result = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def handle_request(self):
            query = parse_qs(urlsplit(browser_urls[0]).query)
            self.callback_result = {"state": query["state"][0], "code": "one-time-code"}

    def respond(request):
        exchanges.append(parse_qs(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": "test-access",
                "token_type": "Bearer",
                "refresh_token": "private-refresh",
                "scope": GMAIL_SEND_SCOPE,
            },
        )

    monkeypatch.setattr("app.integrations.gmail.oauth._CallbackServer", FakeServer)
    monkeypatch.setattr(
        "app.integrations.gmail.oauth.webbrowser.open", browser_urls.append
    )
    GoogleOAuthClient(settings, transport=httpx.MockTransport(respond)).authorize()
    query = parse_qs(urlsplit(browser_urls[0]).query)
    assert query["scope"] == [GMAIL_SEND_SCOPE]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["state"][0]) >= 32
    exchange = exchanges[0]
    assert exchange["code"] == ["one-time-code"]
    assert exchange["grant_type"] == ["authorization_code"]
    assert exchange["redirect_uri"] == [
        f"http://127.0.0.1:{settings.gmail_oauth_port}/oauth/callback"
    ]
    verifier = exchange["code_verifier"][0]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    assert query["code_challenge"] == [challenge]
    token_file = Path(settings.gmail_token_path)
    assert json.loads(token_file.read_text()) == {
        "client_id": settings.gmail_client_id,
        "refresh_token": "private-refresh",
    }
    assert token_file.stat().st_mode & 0o777 == 0o600
    assert token_file.parent.stat().st_mode & 0o777 == 0o700
    assert "private-refresh" not in capsys.readouterr().out
    assert "test-access" not in token_file.read_text()


def test_authorize_rejects_state_mismatch_before_token_exchange(tmp_path, monkeypatch):
    class FakeServer:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def handle_request(self):
            self.callback_result = {"state": "wrong", "code": "one-time-code"}

    monkeypatch.setattr("app.integrations.gmail.oauth._CallbackServer", FakeServer)
    monkeypatch.setattr("app.integrations.gmail.oauth.webbrowser.open", lambda url: None)
    with pytest.raises(DomainError) as error:
        GoogleOAuthClient(
            configuration(tmp_path),
            transport=httpx.MockTransport(lambda request: pytest.fail("No token exchange allowed")),
        ).authorize()
    assert error.value.code == "gmail_oauth_state_invalid"


def test_manual_authorization_prints_url_and_saves_token_without_opening_browser(
    tmp_path, monkeypatch, capsys
):
    settings = configuration(tmp_path, gmail_refresh_token="")

    class FakeServer:
        def __init__(self, *args):
            self.callback_result = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def handle_request(self):
            self.callback_result = {"state": "test-state", "code": "one-time-code"}

    monkeypatch.setattr("app.integrations.gmail.oauth._CallbackServer", FakeServer)
    monkeypatch.setattr(
        "app.integrations.gmail.oauth.secrets.token_urlsafe", lambda length: "test-state"
    )
    monkeypatch.setattr(
        "app.integrations.gmail.oauth.webbrowser.open",
        lambda url: pytest.fail("Manual authorization must preserve the selected browser profile"),
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "access_token": "test-access",
                "token_type": "Bearer",
                "refresh_token": "private-refresh",
                "scope": GMAIL_SEND_SCOPE,
            },
        )
    )
    GoogleOAuthClient(settings, transport=transport).authorize(open_browser=False)
    output = capsys.readouterr().out
    assert "https://accounts.google.com/o/oauth2/v2/auth?" in output
    assert "private-refresh" not in output
    assert read_refresh_token(settings) == "private-refresh"


def test_callback_accepts_only_exact_path_without_logging_codes(capsys):
    from app.integrations.gmail.oauth import _CallbackHandler, _CallbackServer

    with _CallbackServer(("127.0.0.1", 0), _CallbackHandler) as server:
        server.timeout = 2
        worker = threading.Thread(target=lambda: [server.handle_request() for _ in range(2)])
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(trust_env=False, timeout=3) as client:
            assert client.get(url + "/other?code=private-code").status_code == 404
            assert server.callback_result is None
            response = client.get(url + "/oauth/callback?state=state&code=private-code")
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert server.callback_result == {"state": "state", "code": "private-code"}
        captured = capsys.readouterr()
        assert "private-code" not in captured.out + captured.err


def test_authorize_timeout_is_bounded_and_does_not_exchange(tmp_path, monkeypatch):
    class FakeServer:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def handle_request(self):
            pytest.fail("The authorization deadline has passed")

    clock = iter([0, 181])
    monkeypatch.setattr("app.integrations.gmail.oauth.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("app.integrations.gmail.oauth._CallbackServer", FakeServer)
    monkeypatch.setattr("app.integrations.gmail.oauth.webbrowser.open", lambda url: None)
    with pytest.raises(DomainError) as error:
        GoogleOAuthClient(configuration(tmp_path)).authorize()
    assert error.value.code == "gmail_oauth_timeout"
