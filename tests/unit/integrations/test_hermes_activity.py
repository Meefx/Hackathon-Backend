"""Read-only Hermes telemetry fixtures never expose runtime content."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.integrations.agents.contracts import RuntimeError
from app.integrations.hermes.client import HermesClient


@pytest.fixture(autouse=True)
def deterministic_deadline_clock(monkeypatch):
    monkeypatch.setattr("app.integrations.hermes.activity.monotonic", lambda: 0.0)


def frame(sequence, event="tool.started", **fields):
    payload = {
        "event": event,
        "run_id": "run_fixture",
        "timestamp": "2026-11-07T01:00:00+00:00",
        "seq": sequence,
        **fields,
    }
    return f"id: {sequence}\ndata: {json.dumps(payload)}\n\n".encode()


def client_for(content, **kwargs):
    return HermesClient(
        "https://runtime.example", "fixture-secret", profile="manager",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content
            )
        ), **kwargs,
    )


def test_activity_uses_profile_auth_and_native_cursor():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/p/manager/v1/runs/run_fixture/events"
        assert request.url.params["last_seq"] == "4"
        assert request.headers["authorization"] == "Bearer fixture-secret"
        assert request.headers["accept"] == "text/event-stream"
        assert request.extensions["timeout"]["read"] <= 0.5
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=b": open\n\n" + frame(5, "run.started"),
        )

    client = HermesClient(
        "https://runtime.example", "fixture-secret", profile="manager", timeout=60,
        transport=httpx.MockTransport(handler),
    )
    result = client.get_activity("run_fixture", after_sequence=4, limit=100)
    assert result.next_sequence == 5
    assert result.events == [{
        "sequence": 5, "type": "run.started", "agent_id": "manager", "parent_agent_id": None,
        "tool_name": None, "tool_call_id": None, "status": "running", "created_at": datetime(2026, 11, 7, 1, tzinfo=UTC),
    }]
    assert not result.has_more


def test_activity_allows_only_native_identity_and_structural_fields():
    content = (
        frame(0, "subagent.start", subagent_id="child-1", parent_id="manager", goal="PRIVATE")
        + frame(1, "tool.started", tool="search_manager_knowledge", tool_call_id="call-1", args="PRIVATE", preview="PRIVATE")
        + frame(2, "tool.completed", tool="search_manager_knowledge", error=True, output="PRIVATE")
        + frame(3, "subagent.complete", subagent_id="child-1", status="failed", summary="PRIVATE")
        + frame(4, "run.completed", output="PRIVATE")
    )
    result = client_for(content).get_activity("run_fixture")
    assert [event["type"] for event in result.events] == [
        "subagent.start", "tool.started", "tool.completed", "subagent.complete", "run.completed",
    ]
    assert result.events[0]["agent_id"] == "child-1"
    assert result.events[0]["parent_agent_id"] == "manager"
    assert result.events[1]["tool_call_id"] == "call-1"
    assert result.events[2]["status"] == "failed"
    assert result.events[3]["status"] == "failed"
    assert result.events[4]["status"] == "completed"
    assert "PRIVATE" not in repr(result.events)
    assert result.next_sequence == 4


def test_activity_skips_batches_without_child_identity_and_advances_ignored_events():
    content = (
        frame(0, "subagent.start", delegation_id="batch-1", task_count=2)
        + frame(1, "reasoning.available", text="PRIVATE")
        + frame(2, "subagent.start", child_session_id="session-2")
        + frame(3, "run.cancelled")
    )
    result = client_for(content).get_activity("run_fixture")
    assert [event["sequence"] for event in result.events] == [2, 3]
    assert result.events[0]["agent_id"] == "session-2"
    assert result.events[0]["parent_agent_id"] == "manager"
    assert result.next_sequence == 3


def test_activity_ignores_bad_identity_invalid_sequence_and_incomplete_frames():
    content = (
        frame(0, "run.started")
        + frame(10, "run.failed", run_id="another-run")
        + frame(20, "run.failed", seq=True)
        + b'id: 21\ndata: {"event":"run.failed","run_id":"run_fixture","seq":22}\n\n'
        + frame(1, "tool.started", tool="unsafe\nname")
        + frame(2, "subagent.start", subagent_id="unsafe child")
        + b'id: 50\ndata: {"event":"run.completed","run_id":"run_fixture","seq":50}'
    )
    result = client_for(content).get_activity("run_fixture")
    assert [event["sequence"] for event in result.events] == [0]
    assert result.next_sequence == 2


def test_activity_reports_native_replay_truncation_without_inventing_a_sequence():
    marker = b'data: {"event":"replay.truncated","run_id":"run_fixture","oldest_retained_seq":12}\n\n'
    result = client_for(marker + frame(12, "run.failed")).get_activity("run_fixture")
    assert result.truncated
    assert result.next_sequence == 12
    assert result.events[0]["status"] == "failed"


def test_activity_limit_preserves_cursor_for_next_page():
    result = client_for(frame(0, "run.started") + frame(1, "run.completed")).get_activity(
        "run_fixture", limit=1,
    )
    assert [event["sequence"] for event in result.events] == [0]
    assert result.next_sequence == 0
    assert result.has_more


def test_activity_byte_cap_keeps_only_complete_frames():
    content = frame(0, "run.started") + b'data: ' + b'x' * 1_000_000
    result = client_for(content).get_activity("run_fixture")
    assert result.next_sequence == 0
    assert len(result.events) == 1
    assert result.has_more


class IdleStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b": open\n\n"
        yield frame(0, "run.started")
        raise httpx.ReadTimeout("idle stream")


def test_activity_idle_after_complete_frames_is_a_normal_replay_head():
    client = HermesClient("https://runtime.example", "secret", transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=IdleStream(),
        )
    ))
    result = client.get_activity("run_fixture")
    assert result.next_sequence == 0
    assert not result.has_more


@pytest.mark.parametrize("status,code", [
    (302, "runtime_auth_redirect"), (401, "runtime_auth_rejected"),
    (404, "runtime_run_unknown"), (503, "runtime_unavailable"),
])
def test_activity_faults_are_safe_and_do_not_retry(status, code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"location": "/login"}, text="PRIVATE")

    client = HermesClient("https://runtime.example", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError) as error:
        client.get_activity("run_fixture")
    assert error.value.code == code
    assert len(requests) == 1
    assert "PRIVATE" not in str(error.value)


def test_activity_requires_sse_before_returning_available():
    client = HermesClient("https://runtime.example", "secret", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"message": "PRIVATE"})
    ))
    with pytest.raises(RuntimeError, match="runtime_activity_invalid"):
        client.get_activity("run_fixture")


def test_activity_accepts_native_unix_timestamp_and_keeps_unknown_completion_status():
    result = client_for(
        frame(0, "subagent.complete", subagent_id="child-1", status="future-status", timestamp=1794013200.0)
        + frame(1, "run.interrupted")
    ).get_activity("run_fixture")
    assert result.events[0]["created_at"] == datetime(2026, 11, 7, 1, tzinfo=UTC)
    assert result.events[0]["status"] is None
    assert len(result.events) == 1
    assert result.next_sequence == 1


@pytest.mark.parametrize("timestamp", [True, float("nan"), float("inf"), -1e100, 1e100])
def test_activity_rejects_invalid_numeric_timestamp_without_losing_event(timestamp):
    result = client_for(frame(0, "run.started", timestamp=timestamp)).get_activity("run_fixture")
    assert result.events[0]["created_at"] is None


def test_activity_rejects_an_entirely_mismatched_stream():
    with pytest.raises(RuntimeError, match="runtime_activity_invalid"):
        client_for(frame(0, "run.started", run_id="another-run")).get_activity("run_fixture")


class FragmentedStream(httpx.SyncByteStream):
    def __iter__(self):
        content = frame(0, "run.started").replace(b"\n", b"\r\n")
        for offset in range(0, len(content), 7):
            yield content[offset:offset + 7]


def test_activity_accepts_fragmented_crlf_frames():
    client = HermesClient("https://runtime.example", "secret", transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=FragmentedStream(),
        )
    ))
    result = client.get_activity("run_fixture")
    assert result.next_sequence == 0
    assert result.events[0]["type"] == "run.started"


@pytest.mark.parametrize("native,expected", [
    ("completed", "completed"), ("failed", "failed"), ("error", "failed"),
    ("timeout", "failed"), ("interrupted", "failed"), ("cancelled", "cancelled"),
    (None, None), ({"status": "completed"}, None),
])
def test_activity_maps_only_native_completion_status(native, expected):
    result = client_for(frame(
        0, "subagent.complete", subagent_id="child-1", status=native,
    )).get_activity("run_fixture")
    assert result.events[0]["status"] == expected


def test_activity_frame_cap_advances_ignored_frames_without_replaying_them_forever():
    content = b"".join(frame(index, "reasoning.available") for index in range(1100))
    result = client_for(content).get_activity("run_fixture")
    assert result.events == []
    assert result.has_more
    assert result.next_sequence == 999


def test_activity_wall_clock_budget_closes_a_continuously_active_stream(monkeypatch):
    from app.integrations.hermes import activity

    clock = iter([10.0, 11.0, 11.5])
    monkeypatch.setattr(activity, "monotonic", lambda: next(clock))

    class ActiveStream(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(10):
                yield frame(index, "reasoning.available")

    client = HermesClient("https://runtime.example", "secret", transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=ActiveStream(),
        )
    ))
    result = client.get_activity("run_fixture")
    assert result.next_sequence == 1
    assert result.has_more


def test_activity_timeout_before_response_head_is_unavailable():
    def handler(request):
        raise httpx.ReadTimeout("PRIVATE")

    client = HermesClient("https://runtime.example", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="runtime_unavailable"):
        client.get_activity("run_fixture")


def test_activity_deadline_preserves_unread_terminal_event_for_next_page(monkeypatch):
    from app.integrations.hermes import activity

    clock = iter([10.0, 12.0])
    monkeypatch.setattr(activity, "monotonic", lambda: next(clock))
    result = client_for(frame(0, "run.started") + frame(1, "run.completed")).get_activity("run_fixture")
    assert result.next_sequence == 0
    assert result.has_more
    assert [event["type"] for event in result.events] == ["run.started"]
    monkeypatch.setattr(activity, "monotonic", lambda: 20.0)
    next_page = client_for(frame(1, "run.completed")).get_activity(
        "run_fixture", after_sequence=result.next_sequence,
    )
    assert next_page.next_sequence == 1
    assert not next_page.has_more
    assert next_page.events[0]["type"] == "run.completed"


@pytest.mark.parametrize("prior_progress", [True, False])
def test_activity_partial_frame_timeout_requires_continuation_or_explicit_failure(prior_progress):
    class PartialFrameStream(httpx.SyncByteStream):
        def __iter__(self):
            yield frame(0, "run.started") if prior_progress else b": open\n\n"
            yield frame(1, "run.completed")[:-2]
            raise httpx.ReadTimeout("idle during frame")

    client = HermesClient("https://runtime.example", "secret", transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=PartialFrameStream(),
        )
    ))
    if prior_progress:
        result = client.get_activity("run_fixture")
        assert result.next_sequence == 0
        assert result.has_more
        assert len(result.events) == 1
    else:
        with pytest.raises(RuntimeError, match="runtime_activity_incomplete"):
            client.get_activity("run_fixture")
