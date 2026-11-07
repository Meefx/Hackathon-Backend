import base64
import json
from email import policy
from email.parser import BytesParser
from uuid import UUID

import httpx
import pytest

from app.config import Settings
from app.integrations.gmail.client import GmailClient, sandbox_configured
from app.shared.errors import DomainError

REQUEST_ID = UUID("00000000-0000-4000-8000-000000000001")


def settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "gmail_client_id": "synthetic-client",
            "gmail_client_secret": "synthetic-secret",
            "gmail_refresh_token": "synthetic-refresh",
            "gmail_sender_email": "sender@example.test",
            "gmail_test_recipient": "team+demo@example.test",
            **overrides,
        },
    )


def test_gmail_send_encodes_fixed_synthetic_message_and_uses_minimal_contract():
    requests = []

    def provider(request):
        requests.append(request)
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        assert str(request.url) == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
        assert request.headers["authorization"] == "Bearer synthetic-access"
        raw = json.loads(request.content)["raw"]
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        assert message["To"] == "team+demo@example.test"
        assert message["From"] == "sender@example.test"
        assert message["Subject"] == "CollectFlow Gmail connection test"
        assert message["Message-ID"] == f"<collectflow-test-{REQUEST_ID}@example.test>"
        assert message.get_all("Cc") is None
        assert message.get_all("Bcc") is None
        assert "synthetic demo" in message.get_content()
        return httpx.Response(200, json={"id": "gmail123", "threadId": "thread123"})

    client = GmailClient(settings(), transport=httpx.MockTransport(provider))
    assert client.send_test_message(REQUEST_ID) == {"id": "gmail123", "status": "submitted"}
    assert len(requests) == 2


@pytest.mark.parametrize("field", ["gmail_sender_email", "gmail_test_recipient"])
@pytest.mark.parametrize(
    "address",
    [
        "",
        "team",
        "a@example.test,b@example.test",
        "A <a@example.test>",
        "a@example.test\r\nBcc: other@example.test",
    ],
)
def test_invalid_mailboxes_fail_before_contacting_google(field, address):
    requests = []
    client = GmailClient(
        settings(**{field: address}), transport=httpx.MockTransport(requests.append)
    )
    with pytest.raises(DomainError) as error:
        client.send_test_message(REQUEST_ID)
    assert error.value.code == "gmail_mailbox_invalid"
    assert requests == []


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "gmail_request_rejected"),
        (401, "gmail_auth_rejected"),
        (403, "gmail_permission_denied"),
        (429, "gmail_rate_limited"),
        (500, "gmail_delivery_unknown"),
        (302, "gmail_delivery_unknown"),
    ],
)
def test_gmail_send_rejections_never_retry_or_expose_provider_body(status, code):
    sent = []

    def provider(request):
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        sent.append(request)
        return httpx.Response(
            status, text="sensitive-provider-body", headers={"location": "https://other.test"}
        )

    with pytest.raises(DomainError) as error:
        GmailClient(settings(), transport=httpx.MockTransport(provider)).send_test_message(
            REQUEST_ID
        )
    assert error.value.code == code
    assert "sensitive-provider-body" not in str(error.value)
    assert len(sent) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, text="invalid-json"),
        httpx.Response(200, content=b"x" * 65537),
    ],
)
def test_invalid_success_response_is_ambiguous(response):
    def provider(request):
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        return response

    with pytest.raises(DomainError) as error:
        GmailClient(settings(), transport=httpx.MockTransport(provider)).send_test_message(
            REQUEST_ID
        )
    assert error.value.code == "gmail_delivery_unknown"


def test_timeout_after_send_has_unknown_outcome():
    def provider(request):
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "synthetic-access", "token_type": "Bearer"}
            )
        raise httpx.ReadTimeout("sensitive-provider-detail")

    with pytest.raises(DomainError) as error:
        GmailClient(settings(), transport=httpx.MockTransport(provider)).send_test_message(
            REQUEST_ID
        )
    assert error.value.code == "gmail_delivery_unknown"


def test_sandbox_configuration_is_separate_from_live_health():
    assert sandbox_configured(settings())
    assert not sandbox_configured(settings(gmail_client_secret=""))
    assert not sandbox_configured(settings(gmail_test_recipient="invalid"))
