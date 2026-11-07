"""Hermes sandbox admission through domain services and real provider adapters."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name

from uuid import uuid4

import pytest

from app.config import get_settings
from app.db.connection import connect
from app.integrations.agents.contracts import FinalRuntimeResult
from app.modules.collection.service import CollectionService
from app.modules.conversations.schemas import DraftCandidate
from app.modules.runs.repository import finish
from app.modules.runs.schemas import ArtifactCandidate
from app.shared.caller import Caller
from app.workers.collection import claim
from app.workflows.collection import prepare_knowledge, validate_final
from tests.integration.test_email_collection_demo import NOW, PATH, email_demo, provider  # noqa: F401
from tests.integration.test_whatsapp_collection import PEER, link, whatsapp  # noqa: F401


def complete_hermes_run(
    settings,
    text="Halo {borrower_name}, tagihan {loan_reference} "
    "sebesar {outstanding_amount} jatuh tempo {due_date}. Terima kasih.",
):
    run, attempt, fence = claim(settings, NOW, "sandbox-test")
    assert run["requested_runtime_mode"] == "hermes"
    caller = Caller(
        principal_id=settings.mcp_principal_id,
        actor_kind="agent",
        account_id=run["account_id"],
        run_id=run["id"],
        execution_attempt_id=attempt,
        fence=fence,
    )
    snapshot = prepare_knowledge(settings, run, NOW, caller)
    citations = [source["citation_id"] for source in snapshot["sources"]]
    service = CollectionService(settings, lambda: NOW)
    message = service.submit_draft_candidate(
        DraftCandidate(
            run_id=run["id"],
            account_id=run["account_id"],
            execution_attempt_id=attempt,
            text_with_placeholders=text,
            citation_ids=citations,
            idempotency_key=uuid4(),
        ),
        caller,
    )
    artifact = service.submit_run_artifact(
        ArtifactCandidate(
            run_id=run["id"],
            account_id=run["account_id"],
            execution_attempt_id=attempt,
            kind="qa_report",
            text="Synthetic advisory QA",
            citation_ids=citations,
            idempotency_key=uuid4(),
        ),
        caller,
    )
    result = FinalRuntimeResult(
        application_run_id=run["id"],
        task_type="follow_up",
        summary="Synthetic candidate saved",
        artifact_ids=[artifact["id"]],
        candidate_message_id=message["id"],
        citation_ids=citations,
        review_requested=False,
    )
    with connect(settings.database_url) as connection:
        status, outcome, reasons = validate_final(connection, run, result)
        finish(connection, run["id"], status, outcome, result.summary, NOW, reasons)
    return message


def test_hermes_whatsapp_self_delivery_and_replay(whatsapp, monkeypatch):
    client, state = whatsapp
    monkeypatch.setattr("app.modules.whatsapp.router.utcnow", lambda: NOW)
    link(client)
    response = client.post(
        "/api/v1/accounts/CF-1006/runs",
        json={
            "instruction": "Siapkan pengingat sopan",
            "runtime_mode": "hermes",
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 202
    message = complete_hermes_run(get_settings())
    assert message["status"] == "draft"
    assert message["channel"] == "whatsapp"
    path = f"/api/v1/messages/{message['id']}/dispatch"
    body = {"expected_message_version": 1, "idempotency_key": str(uuid4())}
    sent = client.post(path, json=body)
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "submitted", sent.text
    assert client.post(path, json=body).json() == sent.json()
    assert len(state["sent"]) == 1
    assert state["sent"][0]["chatId"] == PEER
    with connect(get_settings().database_url) as connection:
        approval = connection.execute(
            "SELECT * FROM message_approvals WHERE message_id=%s", (message["id"],)
        ).fetchone()
    assert approval["guardrail"]["classifier_mode"] == "sandbox_demo_fixture"


def test_hermes_email_mode_and_exact_retry(email_demo, monkeypatch):
    client, settings = email_demo
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    sent = provider(monkeypatch)
    body = {"idempotency_key": str(uuid4()), "runtime_mode": "hermes"}
    response = client.post(PATH, json=body)
    assert response.status_code == 202, response.text
    demo = response.json()
    assert demo["runtime_mode"] == "hermes"
    message = complete_hermes_run(settings)
    assert message["status"] == "draft"
    from app.modules.email_demo.delivery import tick

    tick(settings, NOW)
    assert client.get(PATH + "/" + demo["id"]).json()["status"] == "submitted"
    assert sent[0].recipient_id == "email:" + settings.gmail_test_recipient
    assert client.post(PATH, json=body).json()["id"] == demo["id"]
    tick(settings, NOW)
    assert len(sent) == 1
    changed = client.post(PATH, json={**body, "runtime_mode": "simulated"})
    assert changed.status_code == 409


@pytest.mark.parametrize("change", ["unlinked", "nonsynthetic", "hostile"])
def test_hermes_sandbox_does_not_approve_other_or_hostile_candidates(whatsapp, monkeypatch, change):
    client, state = whatsapp
    monkeypatch.setattr("app.modules.whatsapp.router.utcnow", lambda: NOW)
    if change != "unlinked":
        link(client)
    if change == "nonsynthetic":
        with connect(get_settings().database_url) as connection:
            connection.execute(
                "UPDATE collection_authorities SET is_synthetic=FALSE WHERE account_id='CF-1006'"
            )
    client.post(
        "/api/v1/accounts/CF-1006/runs",
        json={
            "instruction": "Siapkan pengingat",
            "runtime_mode": "hermes",
            "idempotency_key": str(uuid4()),
        },
    ).raise_for_status()
    text = "Pay now or we will hurt you" if change == "hostile" else "Halo {borrower_name}."
    message = complete_hermes_run(get_settings(), text)
    assert message["status"] == "blocked"
    assert state["sent"] == []


@pytest.mark.parametrize("change", ["pause", "disable", "unlink", "tamper", "expired"])
def test_hermes_whatsapp_rechecks_gate_before_provider(whatsapp, monkeypatch, change):
    from datetime import timedelta

    client, state = whatsapp
    current = [NOW]
    monkeypatch.setattr("app.modules.whatsapp.router.utcnow", lambda: current[0])
    link(client)
    client.post(
        "/api/v1/accounts/CF-1006/runs",
        json={
            "instruction": "Siapkan pengingat",
            "runtime_mode": "hermes",
            "idempotency_key": str(uuid4()),
        },
    ).raise_for_status()
    message = complete_hermes_run(get_settings())
    assert message["status"] == "draft"
    if change == "pause":
        from app.modules.reviews.service import open_review

        with connect(get_settings().database_url) as connection:
            open_review(connection, "CF-1006", "hardship", NOW)
    elif change == "disable":
        settings = get_settings().model_copy(update={"demo_fixture_enabled": False})
        monkeypatch.setattr("app.modules.whatsapp.router.get_settings", lambda: settings)
    elif change == "unlink":
        client.post("/api/v1/accounts/CF-1006/whatsapp/disconnect", json={}).raise_for_status()
    elif change == "tamper":
        with connect(get_settings().database_url) as connection:
            connection.execute(
                "UPDATE collection_messages SET text='Halo' WHERE id=%s", (message["id"],)
            )
    else:
        current[0] += timedelta(seconds=121)
    response = client.post(
        f"/api/v1/messages/{message['id']}/dispatch",
        json={"expected_message_version": 1, "idempotency_key": str(uuid4())},
    )
    assert response.status_code >= 400 or response.json()["status"] == "blocked", response.text
    assert state["sent"] == []


def test_hermes_email_recipient_change_blocks_delivery(email_demo, monkeypatch):
    client, settings = email_demo
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    sent = provider(monkeypatch)
    response = client.post(PATH, json={"idempotency_key": str(uuid4()), "runtime_mode": "hermes"})
    assert response.status_code == 202, response.text
    message = complete_hermes_run(settings)
    assert message["status"] == "draft"
    from app.modules.email_demo.delivery import tick

    tick(settings.model_copy(update={"gmail_test_recipient": "other@example.test"}), NOW)
    result = client.get(PATH + "/" + response.json()["id"]).json()
    assert result["status"] == "failed"
    assert "email_demo_binding_changed" in result["reason_codes"]
    assert sent == []


@pytest.mark.parametrize(
    "scenario,expected_status,accepted",
    [
        ("success", "submitted", 1),
        ("permission_denied", "failed", 0),
        ("server_error", "delivery_unknown", 0),
        ("timeout", "delivery_unknown", 1),
    ],
)
def test_hermes_email_real_http_never_resends(
    email_demo,
    monkeypatch,
    scenario,
    expected_status,
    accepted,
):
    import httpx
    from pydantic import SecretStr

    from app.integrations.gmail.client import GmailClient
    from app.integrations.gmail.oauth import TOKEN_URL
    from app.modules.email_demo.delivery import tick
    from tests.integration.test_gmail_simulation import SIMULATION_URL, LocalGoogleTransport

    if not SIMULATION_URL:
        pytest.skip("Set GMAIL_SIMULATION_URL for local HTTP")

    class GoogleTransport(LocalGoogleTransport):
        def handle_request(self, request):
            # Isolate the modeled send timeout from OAuth under concurrent test load.
            if str(request.url) == TOKEN_URL:
                request.extensions["timeout"] = dict.fromkeys(
                    ("connect", "read", "write", "pool"), 10
                )
            return super().handle_request(request)

    client, settings = email_demo
    settings = settings.model_copy(
        update={
            "gmail_timeout_seconds": 0.1 if scenario == "timeout" else 10,
            "gmail_client_secret": SecretStr("synthetic-secret"),
        }
    )
    monkeypatch.setattr("app.modules.email_demo.router.utcnow", lambda: NOW)
    monkeypatch.setattr("app.modules.email_demo.router.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.integrations.channels.email.GmailClient",
        lambda config: GmailClient(config, transport=GoogleTransport(SIMULATION_URL)),
    )
    control_url = SIMULATION_URL.rstrip("/") + "/google/__control"
    with httpx.Client(trust_env=False, timeout=10) as control:
        control.post(control_url, json={"scenario": scenario}).raise_for_status()
        body = {"idempotency_key": str(uuid4()), "runtime_mode": "hermes"}
        response = client.post(PATH, json=body)
        assert response.status_code == 202, response.text
        message = complete_hermes_run(settings)
        assert message["status"] == "draft"
        for _ in range(3):
            tick(settings, NOW)
            response = client.post(PATH, json=body)
            assert response.status_code == 202, response.text
            assert response.json()["status"] == expected_status, response.text
        assert control.get(control_url).json() == {
            "scenario": scenario,
            "token_requests": 1,
            "send_requests": 1,
            "accepted": accepted,
        }
