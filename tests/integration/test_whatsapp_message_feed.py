from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.db.connection import connect
from app.main import create_app
from app.modules.audit.service import append_event

NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)


@pytest.fixture
def client(database_url):
    with TestClient(create_app()) as client:
        yield client


def test_feed_is_empty_and_validates_pagination(client):
    response = client.get("/api/v1/whatsapp/messages")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 10, "offset": 0}
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert client.get(f"/api/v1/whatsapp/messages?{query}").status_code == 422


def test_feed_reads_outbound_delivery_text_status_and_stable_pages(client, database_url):
    with connect(database_url) as connection:
        connection.execute(
            "INSERT INTO accounts VALUES ('WA-1','Synthetic Borrower','WA-LN-1',"
            "1200000,'2026-11-01','Collector')"
        )
        for message_id, channel, direction, status in (
            ("a", "whatsapp", "outbound", "sent"),
            ("b", "whatsapp", "outbound", "draft"),
            ("draft", "whatsapp", "outbound", "draft"),
            ("inbound", "whatsapp", "inbound", "received"),
            ("email", "email", "outbound", "submitted"),
            ("blocked", "whatsapp", "outbound", "blocked"),
        ):
            connection.execute(
                "INSERT INTO collection_messages "
                "(id,account_id,channel,direction,text,status,created_at) "
                "VALUES (%s,'WA-1',%s,%s,%s,%s,%s)",
                (message_id, channel, direction, "Synthetic agent text " + message_id, status, NOW),
            )
        event = append_event(connection, "WA-1", "whatsapp_dispatch", "operator", "read", NOW)
        connection.execute(
            "INSERT INTO collection_whatsapp_deliveries "
            "(message_id,intent_key,payload_hash,session,chat_id,status,audit_event_id,created_at) "
            "VALUES ('b','test-intent','test-hash','default','synthetic@c.us','read',%s,%s)",
            (event["id"], NOW),
        )
    page = client.get("/api/v1/whatsapp/messages?limit=1").json()
    assert page["total"] == 2
    assert page["items"][0]["borrower_name"] == "Synthetic Borrower"
    assert page["items"][0]["loan_reference"] == "WA-LN-1"
    assert page["items"][0]["message"]["id"] == "b"
    assert page["items"][0]["message"]["text"] == "Synthetic agent text b"
    assert page["items"][0]["message"]["status"] == "read"
    next_page = client.get("/api/v1/whatsapp/messages?limit=1&offset=1").json()
    assert [item["message"]["id"] for item in next_page["items"]] == ["a"]
    assert client.get("/api/v1/whatsapp/messages?offset=2").json()["items"] == []
