"""Gmail sandbox for fixed-recipient connection and approved collection messages."""

import base64
import json
import re
from email import policy
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from uuid import UUID

import httpx

from app.config import Settings
from app.integrations.channels.contracts import AuthorizedIntent
from app.integrations.gmail.oauth import GoogleOAuthClient, read_refresh_token
from app.shared.errors import DomainError

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
TEST_SUBJECT = "CollectFlow Gmail connection test"
TEST_BODY = "CollectFlow Gmail connection test. This is a synthetic demo message."
COLLECTION_SUBJECT = "[DEMO] Pengingat tagihan sintetis"
MAX_RESPONSE_BYTES = 65536


def validate_mailbox(value: str) -> str:
    try:
        value.encode("ascii")
        address = Address(addr_spec=value)
        if not address.username or not address.domain or address.addr_spec != value:
            raise ValueError("Mailbox required")
    except (HeaderParseError, ValueError, UnicodeError):
        raise DomainError(
            "gmail_mailbox_invalid",
            "Configure one plain sender address and one team-owned test recipient",
            422,
        ) from None
    return value


def sandbox_configured(settings: Settings) -> bool:
    try:
        validate_mailbox(settings.gmail_sender_email)
        validate_mailbox(settings.gmail_test_recipient)
        return bool(
            settings.gmail_client_id
            and settings.gmail_client_secret.get_secret_value()
            and read_refresh_token(settings)
        )
    except DomainError:
        return False


class GmailClient:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self._prepared_collection_token: str | None = None

    def prepare_collection(self) -> None:
        self._prepared_collection_token = GoogleOAuthClient(
            self.settings, transport=self.transport
        ).access_token()

    def send_test_message(self, request_id: UUID) -> dict[str, str]:
        sender = validate_mailbox(self.settings.gmail_sender_email)
        recipient = validate_mailbox(self.settings.gmail_test_recipient)
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = TEST_SUBJECT
        message["Message-ID"] = f"<collectflow-test-{request_id}@{sender.rsplit('@', 1)[1]}>"
        message.set_content(TEST_BODY)
        return self._send_message(message)

    def send_collection_message(self, intent: AuthorizedIntent) -> dict[str, str]:
        sender = validate_mailbox(self.settings.gmail_sender_email)
        recipient = validate_mailbox(self.settings.gmail_test_recipient)
        if intent.recipient_id != "email:" + recipient:
            raise DomainError(
                "gmail_recipient_invalid", "Collection recipient must match the team sandbox", 403
            )
        try:
            request_id = UUID(intent.intent_key)
        except (ValueError, TypeError, AttributeError):
            raise DomainError("gmail_intent_invalid", "Collection intent must have a UUID key", 422) from None
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = sender
        message["To"] = recipient
        message["Subject"] = COLLECTION_SUBJECT
        message["Message-ID"] = f"<collectflow-collection-{request_id}@{sender.rsplit('@', 1)[1]}>"
        # Encoding bytes preserves the approved text, including its exact line endings.
        message.set_content(
            intent.text.encode("utf-8"),
            maintype="text",
            subtype="plain",
            cte="base64",
            params={"charset": "utf-8"},
        )
        token = self._prepared_collection_token
        self._prepared_collection_token = None
        return self._send_message(message, token)

    def _send_message(self, message: EmailMessage, token: str | None = None) -> dict[str, str]:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        token = token or GoogleOAuthClient(self.settings, transport=self.transport).access_token()
        try:
            with (
                httpx.Client(
                    transport=self.transport,
                    timeout=self.settings.gmail_timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    "POST",
                    SEND_URL,
                    headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                    json={"raw": raw},
                ) as response,
            ):
                errors = {
                    400: ("gmail_request_rejected", "Gmail rejected the test message"),
                    401: ("gmail_auth_rejected", "Authorize the Google account again"),
                    403: (
                        "gmail_permission_denied",
                        "Check Gmail API access and gmail.send permission",
                    ),
                    429: ("gmail_rate_limited", "Gmail rate limit reached"),
                }
                if response.status_code in errors:
                    code, detail = errors[response.status_code]
                    raise DomainError(code, detail, 502)
                if not 200 <= response.status_code < 300:
                    raise delivery_unknown()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise delivery_unknown()
                try:
                    result = json.loads(body)
                except (ValueError, UnicodeError):
                    raise delivery_unknown() from None
                message_id = result.get("id") if isinstance(result, dict) else None
                if not isinstance(message_id, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{1,128}", message_id
                ):
                    raise delivery_unknown()
                return {"id": message_id, "status": "submitted"}
        except httpx.HTTPError:
            raise delivery_unknown() from None


def delivery_unknown() -> DomainError:
    return DomainError(
        "gmail_delivery_unknown",
        "Gmail acceptance is unconfirmed. Reconcile the sender's Sent folder before another test",
        502,
    )
