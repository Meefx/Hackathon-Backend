from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app
from app.seed import seed


def request_run(client, **overrides):
    payload = {
        "instruction": "Siapkan pengingat sopan",
        "task_type": "follow_up",
        "runtime_mode": "simulated",
        "demo_scenario": "reminder",
        "idempotency_key": str(uuid4()),
    }
    payload.update(overrides)
    return client.post("/api/v1/accounts/CF-1001/runs", json=payload), payload


def test_persistent_simulated_journey_and_exact_retry(database_url):
    seed()
    with TestClient(create_app()) as client:
        response, payload = request_run(client)
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        assert client.post("/api/v1/accounts/CF-1001/runs", json=payload).json() == response.json()
        changed = {**payload, "instruction": "different"}
        assert client.post("/api/v1/accounts/CF-1001/runs", json=changed).status_code == 409
        from app.workers.collection import process_once

        assert process_once(now=datetime(2026, 11, 7, 3, tzinfo=UTC))
        run = client.get(f"/api/v1/runs/{run_id}").json()
        assert run["status"] == "completed", run
        assert run["outcome"] == "draft_created"
        assert run["actual_runtime_mode"] == "simulated"
        messages = client.get("/api/v1/accounts/CF-1001/messages").json()["items"]
        draft = messages[0]
        dispatch = {"expected_message_version": draft["version"], "idempotency_key": str(uuid4())}
        sent = client.post(f"/api/v1/messages/{draft['id']}/simulate-dispatch", json=dispatch)
        # Real contact clock may be outside policy window; explicit clock tests cover allowed gate.
        assert sent.status_code == 200
        assert sent.json()["status"] in {"simulated_sent", "blocked"}
        assert client.get(f"/api/v1/runs/{run_id}/knowledge").json()["items"]
        assert client.get("/api/v1/accounts/CF-1001/audit").json()["items"]


def test_inbound_escalation_pauses_and_review_resolution_is_separate(database_url):
    seed()
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/accounts/CF-1001/messages",
            json={
                "text": "Saya kehilangan pekerjaan",
                "demo_scenario": "hardship",
                "idempotency_key": str(uuid4()),
            },
        )
        assert response.status_code == 201
        policy = client.get("/api/v1/accounts/CF-1001/contact-policy").json()
        assert not policy["allowed"] and policy["contact_state"] == "paused_human_review"
        review = client.get("/api/v1/reviews").json()["items"][0]
        resolved = client.post(
            f"/api/v1/reviews/{review['id']}/resolve",
            json={
                "decision": "keep_paused",
                "note": "Perlu penanganan manusia",
                "expected_version": review["version"],
                "idempotency_key": str(uuid4()),
            },
        )
        assert resolved.status_code == 200
        assert resolved.json()["review"]["status"] == "resolved"
        assert resolved.json()["case"]["contact_state"] == "paused_operator"


def test_cancel_queued_run_is_persisted(database_url):
    seed()
    with TestClient(create_app()) as client:
        response, _ = request_run(client)
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        cancelled = client.post(
            f"/api/v1/runs/{run_id}/cancel",
            json={"expected_version": response.json()["version"], "idempotency_key": str(uuid4())},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
