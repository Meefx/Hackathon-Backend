"""Bounded, content-free projection of the native Hermes SSE replay."""

import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.agents.contracts import RuntimeError

MAX_ACTIVITY_BYTES = 1_000_000
MAX_ACTIVITY_FRAMES = 1000
ACTIVITY_READ_SECONDS = 0.35
ACTIVITY_TOTAL_SECONDS = 1.25
_FRAME_END = re.compile(rb"\r\n\r\n|\n\n|\r\r")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_TOOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}")


@dataclass
class ActivityReplay:
    events: list[dict[str, Any]] = field(default_factory=list)
    next_sequence: int = -1
    has_more: bool = False
    truncated: bool = False


def _safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and _OPAQUE_ID.fullmatch(value) else None


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, UTC) if math.isfinite(value) else None
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _project_event(value: dict[str, Any]) -> dict[str, Any] | None:
    event_type = value.get("event")
    agent_id = "manager"
    parent_id: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    statuses = {
        "run.started": "running", "run.completed": "completed", "run.failed": "failed",
        "run.cancelled": "cancelled",
        "tool.started": "running", "tool.completed": "completed",
        "subagent.start": "running", "subagent.complete": "completed",
    }
    if not isinstance(event_type, str) or event_type not in statuses:
        return None
    status: str | None = statuses[event_type]
    if event_type.startswith("subagent."):
        child_id = _safe_id(value.get("subagent_id")) or _safe_id(value.get("child_session_id"))
        parent_id = _safe_id(value.get("parent_id"))
        if not child_id or child_id == "manager" or ("parent_id" in value and not parent_id):
            return None
        agent_id, parent_id = child_id, parent_id or "manager"
        if event_type == "subagent.complete":
            status = None
            native_status = value.get("status")
            if native_status == "completed":
                status = "completed"
            elif native_status in ("failed", "error", "interrupted", "timeout"):
                status = "failed"
            elif native_status == "cancelled":
                status = "cancelled"
    elif event_type.startswith("tool."):
        tool_name = value.get("tool")
        if not isinstance(tool_name, str) or not _TOOL_NAME.fullmatch(tool_name):
            return None
        tool_call_id = _safe_id(value.get("tool_call_id"))
        if event_type == "tool.completed" and value.get("error") is True:
            status = "failed"
    return {
        "sequence": value["seq"],
        "type": event_type,
        "agent_id": agent_id,
        "parent_agent_id": parent_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "status": status,
        "created_at": _timestamp(value.get("timestamp")),
    }


def _decode_frame(frame: bytes) -> tuple[str | None, dict[str, Any] | None]:
    data, event_id = [], None
    for line in frame.decode("utf-8").splitlines():
        if line.startswith(":") or not line:
            continue
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "data":
            data.append(value)
        elif name == "id":
            event_id = value
    if not data:
        return event_id, None
    value = json.loads("\n".join(data))
    if not isinstance(value, dict):
        raise TypeError
    return event_id, value


def _read_replay(
    response: httpx.Response, external_run_id: str, after_sequence: int, limit: int,
    deadline: float,
) -> ActivityReplay:
    result = ActivityReplay(next_sequence=after_sequence)
    buffer = b""
    bytes_read = frames_read = 0
    received_bytes = valid_frame = invalid_frame = False
    try:
        for chunk in response.iter_bytes():
            received_bytes = True
            remaining = MAX_ACTIVITY_BYTES - bytes_read
            buffer += chunk[:remaining]
            bytes_read += len(chunk)
            frames_before_chunk = frames_read
            while match := _FRAME_END.search(buffer):
                frame, buffer = buffer[:match.start()], buffer[match.end():]
                frames_read += 1
                try:
                    event_id, value = _decode_frame(frame)
                    if value is not None:
                        if value.get("run_id") != external_run_id:
                            raise ValueError
                        if value.get("event") == "replay.truncated" and event_id is None:
                            result.truncated, valid_frame = True, True
                        else:
                            sequence = value.get("seq")
                            if (
                                type(sequence) is not int or sequence < 0 or not event_id
                                or not event_id.isascii() or not event_id.isdecimal()
                                or int(event_id) != sequence
                                or not isinstance(value.get("event"), str)
                            ):
                                raise ValueError
                            valid_frame = True
                            if sequence > result.next_sequence:
                                result.next_sequence = sequence
                                if event := _project_event(value):
                                    result.events.append(event)
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    invalid_frame = True
                if (
                    len(result.events) >= limit or frames_read >= MAX_ACTIVITY_FRAMES
                    or monotonic() >= deadline
                ):
                    result.has_more = True
                    break
            if result.has_more:
                break
            if bytes_read >= MAX_ACTIVITY_BYTES:
                result.has_more = True
                break
            if frames_read == frames_before_chunk and monotonic() >= deadline:
                result.has_more = True
                break
    except httpx.ReadTimeout:
        if not received_bytes:
            raise RuntimeError("runtime_unavailable") from None
    if any(line.strip() and not line.startswith(b":") for line in buffer.splitlines()):
        if result.next_sequence <= after_sequence:
            raise RuntimeError("runtime_activity_incomplete")
        result.has_more = True
    if (invalid_frame or result.has_more) and not valid_frame:
        raise RuntimeError("runtime_activity_invalid")
    return result


def read_activity(
    base_url: str, api_key: str, external_run_id: str, *, after_sequence: int = -1,
    limit: int = 100, transport: httpx.BaseTransport | None = None,
) -> ActivityReplay:
    """Read the current replay head and close the subscription without retrying."""
    if not api_key:
        raise RuntimeError("runtime_not_configured")
    if not _safe_id(external_run_id) or after_sequence < -1 or not 1 <= limit <= 100:
        raise RuntimeError("runtime_activity_invalid")
    deadline = monotonic() + ACTIVITY_TOTAL_SECONDS
    timeout = httpx.Timeout(ACTIVITY_READ_SECONDS, connect=0.5, pool=0.25)
    headers = {
        "Authorization": f"Bearer {api_key}", "Accept": "text/event-stream",
        "Accept-Encoding": "identity",
    }
    try:
        with (
            httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client,
            client.stream(
                "GET", base_url + f"/v1/runs/{quote(external_run_id, safe='')}/events",
                params={"last_seq": after_sequence}, headers=headers,
            ) as response,
        ):
            if response.is_redirect:
                raise RuntimeError("runtime_auth_redirect")
            if response.status_code in (401, 403):
                raise RuntimeError("runtime_auth_rejected")
            if response.status_code == 404:
                raise RuntimeError("runtime_run_unknown")
            if response.status_code >= 400:
                raise RuntimeError("runtime_unavailable")
            if (
                response.headers.get("content-type", "").split(";")[0].strip()
                != "text/event-stream"
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise RuntimeError("runtime_activity_invalid")
            return _read_replay(response, external_run_id, after_sequence, limit, deadline)
    except httpx.RequestError:
        raise RuntimeError("runtime_unavailable") from None
