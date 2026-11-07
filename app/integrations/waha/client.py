"""WAHA local transport. Collection authorization remains in the domain dispatch gate."""

import json
import re
from typing import Any

import httpx

from app.config import Settings
from app.shared.errors import DomainError

MAX_RESPONSE_BYTES = 1_048_576
SESSION_STATES = {
    "STOPPED",
    "STARTING",
    "SCAN_QR_CODE",
    "WORKING",
    "FAILED",
    "PASSKEY_REQUIRED",
    "PASSKEY_CONFIRMATION_REQUIRED",
}
TEST_MESSAGE = "CollectFlow local connection test. This is a synthetic demo message."


class WahaClient:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def _request(
        self, method: str, path: str, *, payload: dict[str, Any] | None = None
    ) -> tuple[bytes, str]:
        key = self.settings.waha_api_key.get_secret_value()
        if not self.settings.waha_base_url or not key:
            raise DomainError("waha_not_configured", "Configure the local WAHA URL and API key", 503)
        try:
            with (
                httpx.Client(
                    timeout=self.settings.waha_timeout_seconds,
                    transport=self.transport,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    method,
                    self.settings.waha_base_url + path,
                    headers={"X-Api-Key": key},
                    json=payload,
                ) as response,
            ):
                if response.is_redirect:
                    raise DomainError("waha_redirect_rejected", "WAHA redirects are rejected", 502)
                if response.status_code in {401, 403}:
                    raise DomainError("waha_auth_rejected", "WAHA API authentication failed", 502)
                if response.status_code == 404:
                    raise DomainError("waha_session_missing", "Create the WAHA session first", 404)
                if method == "POST" and response.status_code >= 500:
                    raise self._invalid_response(method)
                if response.status_code >= 400:
                    raise DomainError("waha_unavailable", "WAHA could not complete the request", 503)
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise self._invalid_response(method)
                return bytes(body), response.headers.get("content-type", "").split(";")[0]
        except httpx.HTTPError:
            if method == "POST":
                raise self._invalid_response(method) from None
            raise DomainError("waha_unavailable", "Local WAHA is unreachable or timed out", 503) from None

    @staticmethod
    def _invalid_response(method: str) -> DomainError:
        if method == "POST":
            return DomainError(
                "waha_delivery_unknown",
                "Delivery is unconfirmed. Check WhatsApp before sending another test",
                502,
            )
        return DomainError("waha_response_invalid", "WAHA returned an invalid response", 502)

    def _json(
        self, method: str, path: str, *, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body, content_type = self._request(method, path, payload=payload)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise self._invalid_response(method) from None
        if content_type != "application/json" or not isinstance(data, dict):
            raise self._invalid_response(method)
        return data

    def session_status(self) -> dict[str, str]:
        session = self.settings.waha_session
        data = self._json("GET", f"/api/sessions/{session}")
        status = data.get("status")
        if data.get("name") != session or not isinstance(status, str) or status not in SESSION_STATES:
            raise self._invalid_response("GET")
        return {"session": session, "status": status}

    def qr_image(self) -> bytes:
        body, content_type = self._request(
            "GET", f"/api/{self.settings.waha_session}/auth/qr?format=image"
        )
        if content_type != "image/png" or not body.startswith(b"\x89PNG\r\n\x1a\n"):
            raise self._invalid_response("GET")
        return body

    def self_chat_id(self) -> str:
        data = self._json("GET", f"/api/sessions/{self.settings.waha_session}")
        chat_id = (data.get("me") or {}).get("id", "")
        if data.get("status") != "WORKING":
            raise DomainError("waha_session_not_ready", "Pair the WAHA session first", 409)
        if not isinstance(chat_id, str) or not re.fullmatch(r"[1-9][0-9]{6,14}@c\.us", chat_id):
            raise DomainError("waha_identity_unavailable", "WAHA account identity is unavailable", 503)
        return chat_id

    def send_text(self, chat_id: str, text: str) -> dict[str, str]:
        if chat_id != self.self_chat_id():
            raise DomainError("waha_recipient_denied", "Only the connected test account is allowed", 403)
        data = self._json("POST", "/api/sendText", payload={
            "session": self.settings.waha_session, "chatId": chat_id, "text": text,
        })
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise self._invalid_response("POST")
        return {"id": data["id"], "status": "submitted"}

    def configure_webhook(self) -> None:
        secret = self.settings.waha_webhook_secret.get_secret_value()
        if len(secret) < 32:
            raise DomainError("waha_webhook_not_configured", "Configure the webhook signing key", 503)
        session = self.settings.waha_session
        data = self._json("GET", f"/api/sessions/{session}")
        config = data.get("config") or {}
        hooks = [hook for hook in config.get("webhooks", []) if hook.get("url") != self.settings.waha_webhook_url]
        hooks.append({"url": self.settings.waha_webhook_url, "events": ["message.any", "message.ack"], "hmac": {"key": secret}, "retries": {"delaySeconds": 3, "attempts": 5, "policy": "exponential"}})
        self._json("PUT", f"/api/sessions/{session}", payload={"name": session, "config": {**config, "webhooks": hooks}})

    def send_test_message(self) -> dict[str, str]:
        recipient = self.settings.waha_test_recipient
        if not re.fullmatch(r"[1-9][0-9]{6,14}", recipient):
            raise DomainError(
                "waha_test_recipient_invalid",
                "Set WAHA_TEST_RECIPIENT to your team-owned international number, such as 628...",
                422,
            )
        if self.session_status()["status"] != "WORKING":
            raise DomainError("waha_session_not_ready", "Pair the WAHA session before testing", 409)
        data = self._json(
            "POST",
            "/api/sendText",
            payload={
                "session": self.settings.waha_session,
                "chatId": recipient + "@c.us",
                "text": TEST_MESSAGE,
            },
        )
        message_id = data.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise self._invalid_response("POST")
        return {"id": message_id, "status": "submitted"}
