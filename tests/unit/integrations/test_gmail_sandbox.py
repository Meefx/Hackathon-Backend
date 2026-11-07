import json
import os
from uuid import UUID

import httpx
import pytest

from app.config import Settings
from app.integrations.gmail.sandbox import send_test
from app.shared.errors import DomainError

REQUEST_ID = UUID("00000000-0000-4000-8000-000000000001")


def settings(tmp_path, **overrides):
    return Settings(
        _env_file=None,
        **{
            "gmail_client_id": "synthetic-client",
            "gmail_client_secret": "synthetic-secret",
            "gmail_refresh_token": "synthetic-refresh",
            "gmail_sender_email": "sender@example.test",
            "gmail_test_recipient": "team@example.test",
            "gmail_token_path": str(tmp_path / "oauth.json"),
            **overrides,
        },
    )


def test_exact_retry_uses_persisted_submission_without_duplicate_send(tmp_path):
    sent = []

    def provider(request):
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        sent.append(request)
        return httpx.Response(200, json={"id": "gmail123"})

    config = settings(tmp_path)
    transport = httpx.MockTransport(provider)
    first = send_test(config, REQUEST_ID, transport=transport)
    assert first == {"id": "gmail123", "status": "submitted"}
    assert send_test(config, REQUEST_ID, transport=transport) == first
    assert len(sent) == 1
    journal = tmp_path / "requests" / f"{REQUEST_ID}.json"
    assert journal.stat().st_mode & 0o777 == 0o600
    assert "team@example.test" not in journal.read_text()
    assert "synthetic-refresh" not in journal.read_text()


def test_reusing_id_with_another_recipient_fails_without_send(tmp_path):
    def provider(request):
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        return httpx.Response(200, json={"id": "gmail123"})

    transport = httpx.MockTransport(provider)
    send_test(settings(tmp_path), REQUEST_ID, transport=transport)
    with pytest.raises(DomainError) as error:
        send_test(
            settings(tmp_path, gmail_test_recipient="another@example.test"),
            REQUEST_ID,
            transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected retry")),
        )
    assert error.value.code == "idempotency_conflict"


def test_ambiguous_send_is_durable_and_cannot_be_retried(tmp_path):
    calls = []

    def provider(request):
        calls.append(request.url.path)
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        raise httpx.ReadTimeout("ambiguous")

    config = settings(tmp_path)
    for _ in range(2):
        with pytest.raises(DomainError) as error:
            send_test(config, REQUEST_ID, transport=httpx.MockTransport(provider))
        assert error.value.code == "gmail_delivery_unknown"
    assert calls.count("/gmail/v1/users/me/messages/send") == 1


def test_inflight_duplicate_fails_closed_before_second_provider_call(tmp_path):
    config = settings(tmp_path)

    def provider(request):
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        with pytest.raises(DomainError) as error:
            send_test(
                config,
                REQUEST_ID,
                transport=httpx.MockTransport(lambda _: pytest.fail("Concurrent send")),
            )
        assert error.value.code == "gmail_delivery_unknown"
        return httpx.Response(200, json={"id": "gmail123"})

    assert (
        send_test(config, REQUEST_ID, transport=httpx.MockTransport(provider))["status"]
        == "submitted"
    )


@pytest.mark.parametrize("contents", ["", "bad-json", json.dumps({"status": "started"})])
def test_corrupt_or_incomplete_journal_never_sends(tmp_path, contents):
    directory = tmp_path / "requests"
    directory.mkdir()
    (directory / f"{REQUEST_ID}.json").write_text(contents)
    with pytest.raises(DomainError):
        send_test(
            settings(tmp_path),
            REQUEST_ID,
            transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected provider call")),
        )


def test_journal_symlink_is_rejected(tmp_path):
    target = tmp_path / "sensitive.json"
    target.write_text("private")
    directory = tmp_path / "requests"
    directory.mkdir()
    (directory / f"{REQUEST_ID}.json").symlink_to(target)
    with pytest.raises(DomainError):
        send_test(settings(tmp_path), REQUEST_ID)
    assert target.read_text() == "private"


def test_journal_fifo_fails_without_blocking(tmp_path):
    directory = tmp_path / "requests"
    directory.mkdir()
    os.mkfifo(directory / f"{REQUEST_ID}.json", 0o600)
    with pytest.raises(DomainError):
        send_test(settings(tmp_path), REQUEST_ID)


def test_failed_journal_missing_code_fails_closed(tmp_path):
    def provider(request):
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        return httpx.Response(200, json={"id": "gmail123"})

    config = settings(tmp_path)
    send_test(config, REQUEST_ID, transport=httpx.MockTransport(provider))
    path = tmp_path / "requests" / f"{REQUEST_ID}.json"
    stored = json.loads(path.read_text())
    stored["status"] = "failed"
    path.write_text(json.dumps(stored))
    with pytest.raises(DomainError) as error:
        send_test(config, REQUEST_ID)
    assert error.value.code == "gmail_delivery_unknown"
