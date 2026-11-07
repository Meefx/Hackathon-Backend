import hashlib
import hmac
import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.connection import connect
from app.main import create_app
from app.seed import seed
from app.workers.collection import process_once

NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)
PEER = "6280000000000@c.us"
SECRET = "synthetic-webhook-secret-material-123"


@pytest.fixture
def whatsapp(database_url, monkeypatch):
    state = {"sent": [], "send_status": 201}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, payload):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def do_GET(self):
            self.respond(200, {"name": "default", "status": "WORKING", "me": {"id": PEER}})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["sent"].append(payload)
            self.respond(state["send_status"], {"id": "provider-message-1"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("WAHA_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("WAHA_API_KEY", "synthetic-api-key")
    monkeypatch.setenv("WAHA_COLLECTION_ENABLED", "true")
    monkeypatch.setenv("WAHA_WEBHOOK_SECRET", SECRET)
    get_settings.cache_clear()
    seed(NOW.date())
    with TestClient(create_app()) as client:
        yield client, state
    server.shutdown()
    server.server_close()
    thread.join()
    get_settings.cache_clear()


def link(client):
    response = client.post(
        "/api/v1/accounts/CF-1006/whatsapp",
        json={"idempotency_key": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    assert response.json()["linked"]


def draft(client):
    response = client.post(
        "/api/v1/accounts/CF-1006/runs",
        json={
            "instruction": "Siapkan pengingat sopan",
            "task_type": "follow_up",
            "runtime_mode": "simulated",
            "demo_scenario": "reminder",
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    assert process_once(now=NOW)
    messages = client.get("/api/v1/accounts/CF-1006/messages").json()["items"]
    return next(message for message in messages if message["direction"] == "outbound")


def webhook(client, event, payload, *, signature=True):
    body = json.dumps(
        {"id": "event-1", "event": event, "session": "default", "payload": payload}
    ).encode()
    return client.post(
        "/api/v1/webhooks/waha",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Hmac-Algorithm": "sha512",
            "X-Webhook-Hmac": hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
            if signature
            else "wrong-signature",
        },
    )


def test_whatsapp_link_sends_through_gate_and_webhooks_update_conversation(whatsapp, monkeypatch):
    client, provider = whatsapp
    monkeypatch.setattr("app.modules.whatsapp.router.utcnow", lambda: NOW)
    link(client)
    message = draft(client)
    assert message["channel"] == "whatsapp"
    assert message["status"] == "draft"
    body = {"idempotency_key": str(uuid4()), "expected_message_version": message["version"]}
    path = f"/api/v1/messages/{message['id']}/dispatch"
    result = client.post(path, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "submitted"
    assert client.post(path, json=body).json() == result.json()
    assert len(provider["sent"]) == 1
    assert provider["sent"][0]["chatId"] == PEER
    assert provider["sent"][0]["text"] == message["text"]
    assert webhook(
        client, "message.ack", {"id": "provider-message-1", "from": PEER, "ack": 3}
    ).status_code == 200
    rows = client.get("/api/v1/accounts/CF-1006/messages").json()["items"]
    assert next(row for row in rows if row["id"] == message["id"])["status"] == "read"
    inbound = {
        "id": "phone-message-1", "from": PEER, "to": PEER, "fromMe": True,
        "source": "app", "body": "Saya PHK dan tidak ada uang", "timestamp": NOW.timestamp(),
    }
    assert webhook(client, "message.any", inbound).status_code == 200
    assert webhook(client, "message.any", inbound).status_code == 200
    rows = client.get("/api/v1/accounts/CF-1006/messages").json()["items"]
    assert len([row for row in rows if row["direction"] == "inbound"]) == 1
    assert client.get("/api/v1/accounts/CF-1006/contact-policy").json()["contact_state"] == "paused_human_review"


def test_whatsapp_unknown_delivery_is_not_retried(whatsapp, monkeypatch):
    client, provider = whatsapp
    monkeypatch.setattr("app.modules.whatsapp.router.utcnow", lambda: NOW)
    link(client)
    message = draft(client)
    provider["send_status"] = 500
    body = {"idempotency_key": str(uuid4()), "expected_message_version": message["version"]}
    path = f"/api/v1/messages/{message['id']}/dispatch"
    result = client.post(path, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "delivery_unknown"
    assert client.post(path, json=body).json() == result.json()
    assert client.post(path, json={**body, "idempotency_key": str(uuid4())}).status_code == 409
    assert len(provider["sent"]) == 1


def test_whatsapp_webhook_rejects_signature_and_ignores_unmapped_sender(whatsapp):
    client, _ = whatsapp
    payload = {"id": "unmapped", "from": "6289999999999@c.us", "body": "private"}
    assert webhook(client, "message.any", payload, signature=False).status_code == 401
    assert webhook(client, "message.any", payload).json()["status"] == "ignored"
    with connect(get_settings().database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_messages").fetchone()["n"] == 0
