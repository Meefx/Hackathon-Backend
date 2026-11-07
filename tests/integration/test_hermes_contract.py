"""Hermes Runs protocol fixtures; these do not claim a live model execution."""

import json
from uuid import uuid4

import httpx
import pytest


def runtime(handler):
    from app.integrations.hermes.client import HermesClient

    return HermesClient(
        "https://runtime.example", "fixture-secret", transport=httpx.MockTransport(handler)
    )


def request():
    from app.integrations.agents.contracts import RuntimeRequest

    return RuntimeRequest(
        application_run_id=str(uuid4()),
        account_id="ACC-001",
        execution_attempt_id=str(uuid4()),
        fence=1,
        task_type="collector_brief",
        instruction="Buat ringkasan akun sintetis.",
        history=[{"role": "system", "content": "Synthetic collection demo; backend gates apply."}],
    )


def test_probe_rejects_admin_login_instead_of_claiming_healthy():
    client = runtime(lambda r: httpx.Response(302, headers={"location": "/login"}))
    result = client.probe()
    assert not result.available
    assert result.reason_code == "runtime_auth_redirect"


def test_probe_pins_actual_runs_capabilities():
    def handler(r):
        assert r.headers["authorization"] == "Bearer fixture-secret"
        assert r.url.path == "/v1/capabilities"
        return httpx.Response(
            200,
            json={
                "object": "hermes.api_server.capabilities",
                "platform": "hermes-agent",
                "features": {"run_submission": True, "run_status": True, "run_stop": True},
            },
        )

    probe = runtime(handler).probe()
    assert probe.available and probe.actual_runtime_mode == "hermes"
    assert set(probe.capabilities) == {"run_submission", "run_status", "run_stop"}


def test_submit_sends_nonempty_history_and_exact_replay_payload():
    seen = []

    def handler(r):
        seen.append(r)
        return httpx.Response(202, json={"run_id": "run_fixture", "status": "started"})

    client = runtime(handler)
    task = request()
    first = client.submit(task, "stable-key")
    second = client.submit(task, "stable-key")
    assert first.external_run_id == second.external_run_id == "run_fixture"
    assert seen[0].content == seen[1].content
    assert seen[0].headers["idempotency-key"] == "stable-key"
    payload = json.loads(seen[0].content)
    assert payload["conversation_history"] == [x.model_dump() for x in task.history]
    assert "session_id" not in payload
    assert "application_run_id" in payload["instructions"]


def test_empty_history_is_refused_before_admission():
    from pydantic import ValidationError

    from app.integrations.agents.contracts import RuntimeRequest

    with pytest.raises(ValidationError):
        RuntimeRequest(**{**request().model_dump(), "history": []})


@pytest.mark.parametrize(
    "external,expected",
    [
        ("unknown", "interrupted"),
        ("interrupted", "interrupted"),
        ("stopping", "running"),
        ("cancelled", "cancelled"),
        ("failed", "failed"),
    ],
)
def test_status_never_treats_ambiguous_or_stopping_as_success(external, expected):
    status = runtime(
        lambda r: httpx.Response(
            200,
            json={
                "object": "hermes.run",
                "run_id": "run_fixture",
                "status": external,
            },
        )
    ).get_status("run_fixture")
    assert status.status == expected
    assert status.final_result is None


def test_completed_runtime_requires_schema_valid_business_result():
    status = runtime(
        lambda r: httpx.Response(
            200,
            json={
                "run_id": "run_fixture",
                "status": "completed",
                "output": "Done.",
            },
        )
    ).get_status("run_fixture")
    assert status.status == "failed"
    assert status.reason_code == "result_invalid"


def test_final_result_and_served_model_are_validated():
    task = request()
    output = {
        "application_run_id": task.application_run_id,
        "task_type": task.task_type,
        "summary": "Ringkasan tersedia.",
        "artifact_ids": [str(uuid4())],
        "candidate_message_id": None,
        "citation_ids": [],
        "review_requested": False,
    }
    status = runtime(
        lambda r: httpx.Response(
            200,
            json={
                "run_id": "run_fixture",
                "status": "completed",
                "output": json.dumps(output),
                "runtime": {
                    "provider": "fixture",
                    "model": "fixture-model",
                    "route_source": "global",
                },
                "usage": {"input_tokens": 5, "output_tokens": 10, "total_tokens": 15},
            },
        )
    ).get_status("run_fixture")
    assert status.status == "completed"
    assert status.final_result.application_run_id == task.application_run_id
    assert status.runtime["model"] == "fixture-model"


def test_stop_acknowledgement_is_only_a_request():
    seen = []

    def handler(r):
        seen.append(r)
        return httpx.Response(200, json={"status": "stopping"})

    status = runtime(handler).request_stop("run_fixture")
    assert seen[0].url.path == "/v1/runs/run_fixture/stop"
    assert status.status == "running"


def test_network_ambiguity_does_not_retry_submission():
    from app.integrations.agents.contracts import RuntimeError

    seen = []

    def handler(r):
        seen.append(r)
        raise httpx.ReadTimeout("fixture")

    with pytest.raises(RuntimeError) as exc:
        runtime(handler).submit(request(), "stable-key")
    assert exc.value.code == "runtime_submission_unknown"
    assert exc.value.ambiguous
    assert len(seen) == 1


@pytest.mark.parametrize("features", [None, [], "claimed-by-model"])
def test_malformed_capabilities_are_unavailable(features):
    probe = runtime(
        lambda r: httpx.Response(
            200,
            json={
                "object": "hermes.api_server.capabilities",
                "features": features,
            },
        )
    ).probe()
    assert not probe.available


@pytest.mark.parametrize("status", [None, [], {"completed": True}, "new-future-state"])
def test_untrusted_status_shape_is_interrupted(status):
    value = runtime(
        lambda r: httpx.Response(
            200,
            json={
                "run_id": "run_fixture",
                "status": status,
            },
        )
    ).get_status("run_fixture")
    assert value.status == "interrupted"


def test_final_result_does_not_coerce_untrusted_boolean_claims():
    output = {
        "application_run_id": "run-1",
        "task_type": "collector_brief",
        "summary": "Ringkasan",
        "artifact_ids": [],
        "candidate_message_id": None,
        "citation_ids": [],
        "review_requested": "false",
    }
    status = runtime(
        lambda r: httpx.Response(
            200,
            json={
                "run_id": "run_fixture",
                "status": "completed",
                "output": json.dumps(output),
            },
        )
    ).get_status("run_fixture")
    assert status.status == "failed"
    assert status.reason_code == "result_invalid"


def test_poll_network_loss_preserves_accepted_run_ambiguity():
    from app.integrations.agents.contracts import RuntimeError

    def handler(r):
        raise httpx.ReadTimeout("fixture")

    with pytest.raises(RuntimeError) as exc:
        runtime(handler).get_status("run_already_admitted")
    assert exc.value.ambiguous


def test_stop_auth_failure_does_not_prove_accepted_run_stopped():
    from app.integrations.agents.contracts import RuntimeError

    with pytest.raises(RuntimeError) as exc:
        runtime(lambda r: httpx.Response(401)).request_stop("run_already_admitted")
    assert exc.value.ambiguous


@pytest.mark.parametrize("status", ["stopping", "cancelled"])
def test_stop_rejects_acknowledgement_for_different_run(status):
    from app.integrations.agents.contracts import RuntimeError

    with pytest.raises(RuntimeError) as exc:
        runtime(
            lambda r: httpx.Response(
                200,
                json={
                    "run_id": "different_run",
                    "status": status,
                },
            )
        ).request_stop("run_requested")
    assert exc.value.code == "runtime_response_invalid"
    assert exc.value.ambiguous


@pytest.mark.parametrize("confirmed_status", ["running", "cancelled"])
def test_stop_without_identity_cannot_report_terminal_until_poll_confirms(confirmed_status):
    seen = []

    def handler(r):
        seen.append((r.method, r.url.path))
        if r.method == "POST":
            return httpx.Response(200, json={"status": "cancelled"})
        return httpx.Response(200, json={"run_id": "run_requested", "status": confirmed_status})

    result = runtime(handler).request_stop("run_requested")
    assert result.status == confirmed_status
    assert seen == [("POST", "/v1/runs/run_requested/stop"), ("GET", "/v1/runs/run_requested")]


def test_identity_omitted_terminal_stop_with_unconfirmed_poll_remains_ambiguous():
    from app.integrations.agents.contracts import RuntimeError

    def handler(r):
        return httpx.Response(200, json={"status": "cancelled"})

    with pytest.raises(RuntimeError) as exc:
        runtime(handler).request_stop("run_requested")
    assert exc.value.code == "runtime_response_invalid"
    assert exc.value.ambiguous


def test_documented_approval_wait_keeps_execution_active():
    status = runtime(
        lambda r: httpx.Response(
            200,
            json={
                "run_id": "run_waiting",
                "status": "waiting_for_approval",
                "pending_controls": [{"kind": "approval", "prompt_id": "sensitive-prompt"}],
            },
        )
    ).get_status("run_waiting")
    assert status.status == "running"
    assert status.reason_code == "runtime_approval_required"
    assert status.final_result is None
    assert "sensitive-prompt" not in status.model_dump_json()


def test_documented_clarification_projection_stays_running():
    # Official authority run projection leaves clarification in running and
    # exposes the separate prompt only in pending_controls.
    status = runtime(
        lambda r: httpx.Response(
            200,
            json={
                "run_id": "run_waiting",
                "status": "running",
                "pending_controls": [{"kind": "clarify", "prompt_id": "sensitive-prompt"}],
            },
        )
    ).get_status("run_waiting")
    assert status.status == "running"
    assert status.final_result is None
    assert "sensitive-prompt" not in status.model_dump_json()
