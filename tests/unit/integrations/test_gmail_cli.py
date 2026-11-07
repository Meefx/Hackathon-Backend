import json
from uuid import UUID

import pytest

from app.config import Settings
from app.integrations.gmail import __main__ as cli
from app.shared.errors import DomainError


def test_cli_missing_credentials_returns_sanitized_error(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Settings", lambda **_: Settings(_env_file=None))
    assert cli.main(["status"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "gmail_not_configured" in captured.err
    assert "Traceback" not in captured.err


def test_cli_requires_explicit_uuid_before_sending(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["send-test"])
    assert error.value.code == 2
    assert "--request-id" in capsys.readouterr().err


def test_cli_passes_exact_request_id_and_only_outputs_safe_receipt(monkeypatch, capsys):
    request_id = "00000000-0000-4000-8000-000000000001"
    settings = Settings(_env_file=None, gmail_client_secret="synthetic-private")
    monkeypatch.setattr(cli, "Settings", lambda **_: settings)
    calls = []

    def sender(config, key):
        calls.append((config, key))
        return {"id": "gmail123", "status": "submitted"}

    monkeypatch.setattr(cli, "send_test", sender)
    assert cli.main(["send-test", "--request-id", request_id]) == 0
    assert calls == [(settings, UUID(request_id))]
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "request_id": request_id,
        "id": "gmail123",
        "status": "submitted",
    }
    assert "synthetic-private" not in captured.out + captured.err


def test_cli_ambiguous_failure_is_an_error_exit(monkeypatch, capsys):
    def sender(*_):
        raise DomainError("gmail_delivery_unknown", "Reconcile the previous test", 502)

    monkeypatch.setattr(cli, "send_test", sender)
    assert cli.main(["send-test", "--request-id", "00000000-0000-4000-8000-000000000001"]) == 1
    captured = capsys.readouterr()
    assert "gmail_delivery_unknown" in captured.err
    assert captured.out == ""


def test_cli_manual_authorization_preserves_browser_choice(monkeypatch, capsys):
    browser_options = []

    def authorize(self, *, open_browser=True):
        browser_options.append(open_browser)

    monkeypatch.setattr(cli.GoogleOAuthClient, "authorize", authorize)
    assert cli.main(["authorize", "--no-browser"]) == 0
    assert browser_options == [False]
    assert json.loads(capsys.readouterr().out) == {"status": "authorized"}
