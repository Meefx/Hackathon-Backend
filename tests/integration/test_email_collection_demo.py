from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db.connection import connect
from app.main import create_app
from app.seed import seed
from app.shared.errors import DomainError
from app.workers.collection import process_cycle, process_once

NOW = datetime(2026, 11, 7, 3, tzinfo=UTC)
PATH = "/api/v1/integrations/gmail/collection-demo"


@pytest.fixture
def email_demo(database_url, monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for key, value in {
        "GMAIL_CLIENT_ID": "synthetic-client",
        "GMAIL_CLIENT_SECRET": "synthetic-client-secret",
        "GMAIL_REFRESH_TOKEN": "synthetic-refresh",
        "GMAIL_SENDER_EMAIL": "sender@example.test",
        "GMAIL_TEST_RECIPIENT": "team@example.test",
        "SCHEDULER_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    seed(NOW.date())
    with TestClient(create_app()) as client:
        yield client, get_settings()
    get_settings.cache_clear()


def admit(client, key=None):
    result = client.post(PATH, json={"idempotency_key": str(key or uuid4())})
    assert result.status_code == 202, result.text
    return result.json()


def provider(monkeypatch, failure=None):
    sent = []
    monkeypatch.setattr("app.integrations.channels.email.EmailAdapter.prepare", lambda self: None)

    def send(self, intent):
        sent.append(intent)
        if failure:
            raise DomainError(failure, "Synthetic provider failure", 502)
        return {"id": "gmail-provider-" + intent.message_id, "status": "submitted"}

    monkeypatch.setattr("app.integrations.channels.email.EmailAdapter.dispatch", send)
    return sent


def test_email_demo_admission_is_replayable_and_repeated_click_creates_fresh_case(
    email_demo, monkeypatch
):
    client, settings = email_demo
    assert client.get(PATH).status_code == 200
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    key = uuid4()
    first = admit(client, key)
    assert first["status"] == "queued"
    assert first["runtime_mode"] == "simulated"
    assert first["recipient_masked"] != settings.gmail_test_recipient
    assert admit(client, key) == first
    second = admit(client)
    assert second["account_id"] != first["account_id"]
    with connect(settings.database_url) as connection:
        assert (
            connection.execute("SELECT count(*) n FROM collection_email_demos").fetchone()["n"] == 2
        )
        assert connection.execute("SELECT count(*) n FROM collection_runs").fetchone()["n"] == 2
        account = connection.execute(
            "SELECT * FROM accounts WHERE id=%s", (first["account_id"],)
        ).fetchone()
        assert account["due_date"] == NOW.date()
        authority = connection.execute(
            "SELECT * FROM collection_authorities WHERE account_id=%s", (first["account_id"],)
        ).fetchone()
        assert authority["is_synthetic"] and authority["verified"]
    from app.modules.scheduling.service import tick

    tick(settings.model_copy(update={"scheduler_enabled": True}), NOW + timedelta(days=1))
    with connect(settings.database_url) as connection:
        assert not connection.execute(
            "SELECT 1 FROM collection_schedule_items WHERE account_id=%s", (first["account_id"],)
        ).fetchone()


def test_email_demo_concurrent_retry_creates_one_account_and_run(email_demo, monkeypatch):
    client, settings = email_demo
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    key = uuid4()
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: admit(client, key), range(4)))
    assert len({row["id"] for row in results}) == 1
    with connect(settings.database_url) as connection:
        assert (
            connection.execute("SELECT count(*) n FROM collection_email_demos").fetchone()["n"] == 1
        )
        assert connection.execute("SELECT count(*) n FROM collection_jobs").fetchone()["n"] == 1


def test_email_demo_worker_sends_exact_approved_draft_and_reads_do_not_send(
    email_demo, monkeypatch
):
    client, settings = email_demo
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    sent = provider(monkeypatch)
    demo = admit(client)
    assert process_cycle(settings, now=NOW)
    current = client.get(PATH + "/" + demo["id"]).json()
    assert current["status"] == "submitted", current
    assert len(sent) == 1
    with connect(settings.database_url) as connection:
        message = connection.execute(
            "SELECT * FROM collection_messages WHERE id=%s", (current["message_id"],)
        ).fetchone()
        assert message["channel"] == "email"
        assert message["text"].startswith("[DEMO SINTETIS]")
        assert sent[0].text == message["text"]
        assert sent[0].recipient_id == "email:" + settings.gmail_test_recipient
        binding = connection.execute(
            "SELECT binding FROM message_approvals WHERE message_id=%s", (message["id"],)
        ).fetchone()["binding"]
        assert binding["channel"] == "email"
        assert binding["recipient_id"] == sent[0].recipient_id
        assert connection.execute(
            "SELECT 1 FROM dispatch_receipts WHERE message_id=%s", (message["id"],)
        ).fetchone()
        assert connection.execute(
            "SELECT 1 FROM audit_events WHERE account_id=%s AND action='email_demo_dispatch'",
            (demo["account_id"],),
        ).fetchone()
    for _ in range(2):
        assert client.get(PATH).json()["latest"]["id"] == demo["id"]
        client.get(PATH + "/" + demo["id"])
        process_cycle(settings, now=NOW)
    assert len(sent) == 1


@pytest.mark.parametrize(
    "failure,expected",
    [("gmail_permission_denied", "failed"), ("gmail_delivery_unknown", "delivery_unknown")],
)
def test_email_demo_provider_failure_is_durable_and_never_resent(
    email_demo, monkeypatch, failure, expected
):
    client, settings = email_demo
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    sent = provider(monkeypatch, failure)
    key = uuid4()
    demo = admit(client, key)
    process_cycle(settings, now=NOW)
    result = client.get(PATH + "/" + demo["id"]).json()
    assert result["status"] == expected
    assert failure in result["reason_codes"]
    for _ in range(2):
        admit(client, key)
        process_cycle(settings, now=NOW)
    assert len(sent) == 1
    if expected == "delivery_unknown":
        from app.modules.compliance.service import evaluate_contact_policy

        with connect(settings.database_url) as connection:
            decision = evaluate_contact_policy(connection, demo["account_id"], NOW)
        assert not decision["allowed"]
        assert "frequency_cap_daily" in decision["reason_codes"]


def test_email_demo_hold_before_dispatch_blocks_provider(email_demo, monkeypatch):
    client, settings = email_demo
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    sent = provider(monkeypatch)
    demo = admit(client)
    assert process_once(settings, now=NOW)
    with connect(settings.database_url) as connection:
        from app.modules.reviews.service import open_review

        open_review(connection, demo["account_id"], "hardship", NOW)
    from app.modules.email_demo.delivery import tick

    tick(settings, NOW)
    result = client.get(PATH + "/" + demo["id"]).json()
    assert result["status"] == "blocked"
    assert "case_paused" in result["reason_codes"]
    assert sent == []


def test_email_demo_rejects_extra_fields_unconfigured_and_disabled_without_writes(
    email_demo, monkeypatch
):
    client, settings = email_demo
    response = client.post(
        PATH, json={"idempotency_key": str(uuid4()), "recipient": "other@example.test"}
    )
    assert response.status_code == 422
    for changes in ({"gmail_client_id": ""}, {"demo_fixture_enabled": False}):
        unavailable = settings.model_copy(update=changes)
        monkeypatch.setattr(
            "app.modules.email_demo.router.get_settings",
            lambda unavailable=unavailable: unavailable,
        )
        response = client.post(PATH, json={"idempotency_key": str(uuid4())})
        assert response.status_code == 503
    with connect(settings.database_url) as connection:
        assert (
            connection.execute("SELECT count(*) n FROM collection_email_demos").fetchone()["n"] == 0
        )


def test_email_demo_accepted_uuid_can_recover_after_configuration_is_disabled(
    email_demo, monkeypatch
):
    client, settings = email_demo
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    key = uuid4()
    demo = admit(client, key)
    monkeypatch.setattr(
        "app.modules.email_demo.router.get_settings",
        lambda: settings.model_copy(update={"demo_fixture_enabled": False}),
    )
    recovered = client.post(PATH, json={"idempotency_key": str(key)})
    assert recovered.status_code == 202
    assert recovered.json()["id"] == demo["id"]


def test_email_demo_reservation_is_committed_but_running_during_provider_io(
    email_demo, monkeypatch
):
    client, settings = email_demo
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    sent = provider(monkeypatch)
    demo = admit(client)
    from app.modules.email_demo import delivery

    original = delivery.dispatch_message

    def inspect_reservation(*args, **kwargs):
        assert client.get(PATH + "/" + demo["id"]).json()["status"] == "running"
        with connect(settings.database_url) as connection:
            row = connection.execute(
                "SELECT reserved_at FROM collection_email_demos WHERE id=%s", (demo["id"],)
            ).fetchone()
            assert row["reserved_at"] == NOW
        return original(*args, **kwargs)

    monkeypatch.setattr(delivery, "dispatch_message", inspect_reservation)
    process_cycle(settings, now=NOW)
    assert len(sent) == 1
    assert client.get(PATH + "/" + demo["id"]).json()["status"] == "submitted"


def test_email_demo_commit_ambiguity_keeps_reservation_and_prevents_resend(email_demo, monkeypatch):
    client, settings = email_demo
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    sent = provider(monkeypatch)
    demo = admit(client)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("Synthetic commit failure after provider accepted")

    monkeypatch.setattr("app.modules.conversations.dispatch.append_event", fail_audit)
    process_cycle(settings, now=NOW)
    from app.modules.email_demo.delivery import tick

    tick(settings, NOW + timedelta(seconds=181))
    result = client.get(PATH + "/" + demo["id"]).json()
    assert result["status"] == "delivery_unknown"
    with connect(settings.database_url) as connection:
        assert not connection.execute(
            "SELECT 1 FROM dispatch_receipts WHERE account_id=%s", (demo["account_id"],)
        ).fetchone()
    tick(settings, NOW + timedelta(seconds=182))
    assert len(sent) == 1


def test_email_demo_concurrent_delivery_workers_do_not_duplicate_send(email_demo, monkeypatch):
    client, settings = email_demo
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    sent = provider(monkeypatch)
    demo = admit(client)
    process_once(settings, now=NOW)
    from app.modules.email_demo.delivery import tick

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda _: tick(settings, NOW), range(3)))
    assert len(sent) == 1
    assert client.get(PATH + "/" + demo["id"]).json()["status"] == "submitted"


def test_email_demo_expired_approval_and_generic_dispatch_never_send(email_demo, monkeypatch):
    client, settings = email_demo
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    sent = provider(monkeypatch)
    demo = admit(client)
    process_once(settings, now=NOW)
    result = client.get(PATH + "/" + demo["id"]).json()
    response = client.post(
        f"/api/v1/messages/{result['message_id']}/simulate-dispatch",
        json={"idempotency_key": str(uuid4()), "expected_message_version": 1},
    )
    assert response.status_code == 422
    from app.modules.email_demo.delivery import tick

    tick(settings, NOW + timedelta(minutes=3))
    result = client.get(PATH + "/" + demo["id"]).json()
    assert result["status"] == "blocked"
    assert "approval_expired" in result["reason_codes"]
    assert sent == []
