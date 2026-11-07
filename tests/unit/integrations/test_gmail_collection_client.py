import base64
import json
from dataclasses import replace
from email import policy
from email.parser import BytesParser

import httpx
import pytest

from app.config import Settings
from app.integrations.channels import email
from app.integrations.channels.contracts import AuthorizedIntent
from app.integrations.channels.email import EmailAdapter
from app.integrations.gmail.client import GmailClient
from app.shared.errors import DomainError

INTENT = AuthorizedIntent(
    intent_key="00000000-0000-4000-8000-000000000001",
    message_id="synthetic-message",
    account_id="synthetic-account",
    recipient_id="email:team@example.test",
    text="[DEMO] Pengingat sintetis untuk Rp150.000.\nSilakan hubungi petugas bila perlu.",
)


def settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "demo_fixture_enabled": True,
            "gmail_client_id": "synthetic-client",
            "gmail_client_secret": "synthetic-secret",
            "gmail_refresh_token": "synthetic-refresh",
            "gmail_sender_email": "sender@example.test",
            "gmail_test_recipient": "team@example.test",
            **overrides,
        },
    )


@pytest.mark.parametrize("text", [INTENT.text, "Body\r\nBcc: other@example.test\r\nPesan é"])
def test_collection_send_preserves_approved_body_and_fixed_recipient(text):
    sends = []

    def provider(request):
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        assert str(request.url) == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
        assert request.headers["authorization"] == "Bearer synthetic-access"
        sends.append(request)
        raw = json.loads(request.content)["raw"]
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        assert message["From"] == "sender@example.test"
        assert message["To"] == "team@example.test"
        assert message["Subject"] == "[DEMO] Pengingat tagihan sintetis"
        assert message["Message-ID"] == (
            "<collectflow-collection-00000000-0000-4000-8000-000000000001@example.test>"
        )
        assert message.get_all("Cc") is None
        assert message.get_all("Bcc") is None
        assert message.get_content_type() == "text/plain"
        assert message.get_payload(decode=True).decode("utf-8") == text
        return httpx.Response(200, json={"id": "gmail-collection123", "threadId": "thread123"})

    result = GmailClient(settings(), transport=httpx.MockTransport(provider)).send_collection_message(
        replace(INTENT, text=text)
    )
    assert result == {"id": "gmail-collection123", "status": "submitted"}
    assert len(sends) == 1


@pytest.mark.parametrize(
    "recipient",
    ["email:other@example.test", "synthetic:synthetic-account", "email:team@example.test\r\nBcc:x@y.test"],
)
def test_collection_recipient_must_match_fixed_backend_mailbox_before_google(recipient):
    requests = []
    client = GmailClient(settings(), transport=httpx.MockTransport(requests.append))
    with pytest.raises(DomainError) as error:
        client.send_collection_message(replace(INTENT, recipient_id=recipient))
    assert error.value.code == "gmail_recipient_invalid"
    assert requests == []


@pytest.mark.parametrize("field", ["gmail_sender_email", "gmail_test_recipient"])
def test_collection_rejects_injected_configured_mailbox_before_google(field):
    requests = []
    client = GmailClient(
        settings(**{field: "team@example.test\r\nBcc: other@example.test"}),
        transport=httpx.MockTransport(requests.append),
    )
    with pytest.raises(DomainError) as error:
        client.send_collection_message(INTENT)
    assert error.value.code == "gmail_mailbox_invalid"
    assert requests == []


def test_collection_rejects_injected_intent_key_before_google():
    requests = []
    client = GmailClient(settings(), transport=httpx.MockTransport(requests.append))
    with pytest.raises(DomainError) as error:
        client.send_collection_message(replace(INTENT, intent_key="invalid\r\nBcc:x@y.test"))
    assert error.value.code == "gmail_intent_invalid"
    assert requests == []


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(403, text="sensitive-provider-body"), "gmail_permission_denied"),
        (httpx.Response(500, text="sensitive-provider-body"), "gmail_delivery_unknown"),
        (httpx.Response(200, json={}), "gmail_delivery_unknown"),
    ],
)
def test_collection_provider_failure_does_not_retry_or_expose_details(response, code):
    sends = []

    def provider(request):
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        sends.append(request)
        return response

    with pytest.raises(DomainError) as error:
        GmailClient(settings(), transport=httpx.MockTransport(provider)).send_collection_message(
            INTENT
        )
    assert error.value.code == code
    assert "sensitive-provider-body" not in str(error.value)
    assert len(sends) == 1


def test_collection_timeout_remains_unknown_without_retry():
    sends = []

    def provider(request):
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        sends.append(request)
        raise httpx.ReadTimeout("sensitive-provider-detail")

    with pytest.raises(DomainError) as error:
        GmailClient(settings(), transport=httpx.MockTransport(provider)).send_collection_message(
            INTENT
        )
    assert error.value.code == "gmail_delivery_unknown"
    assert len(sends) == 1


def test_adapter_prepares_oauth_before_dispatch_without_a_second_refresh(monkeypatch):
    requests = []

    def provider(request):
        requests.append(request.url.path)
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        return httpx.Response(200, json={"id": "gmail123", "threadId": "thread123"})

    transport = httpx.MockTransport(provider)
    monkeypatch.setattr(email, "GmailClient", lambda value: GmailClient(value, transport=transport))
    adapter = EmailAdapter(settings())
    adapter.prepare()
    assert requests == ["/token"]
    assert adapter.dispatch(INTENT) == {"id": "gmail123", "status": "submitted"}
    assert requests == ["/token", "/gmail/v1/users/me/messages/send"]


@pytest.mark.parametrize(
    "configuration",
    [None, settings(demo_fixture_enabled=False), settings(gmail_client_secret="")],
)
def test_email_adapter_preserves_unavailable_skeleton_when_disabled(configuration):
    with pytest.raises(DomainError) as error:
        EmailAdapter(configuration).dispatch(INTENT)
    assert error.value.code == "channel_unavailable"
