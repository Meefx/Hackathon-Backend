"""Durable local request journal for manual Gmail connection tests."""

import json
import os
import re
import stat
from pathlib import Path
from uuid import UUID

import httpx

from app.config import Settings
from app.integrations.gmail.client import (
    TEST_BODY,
    TEST_SUBJECT,
    GmailClient,
    delivery_unknown,
    sandbox_configured,
)
from app.shared.errors import DomainError
from app.shared.idempotency import digest


def _replay(directory_fd: int, filename: str, payload_hash: str) -> dict[str, str]:
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise delivery_unknown()
        try:
            stored = json.loads(source.read(16384))
        except (ValueError, UnicodeError):
            raise delivery_unknown() from None
    if not isinstance(stored, dict) or stored.get("payload_hash") != payload_hash:
        raise DomainError(
            "idempotency_conflict", "Request ID belongs to another test configuration"
        )
    message_id = stored.get("id")
    if (
        stored.get("status") == "submitted"
        and isinstance(message_id, str)
        and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message_id)
    ):
        return {"id": stored["id"], "status": "submitted"}
    code = stored.get("code")
    if (
        stored.get("status") == "failed"
        and isinstance(code, str)
        and re.fullmatch(r"gmail_[a-z_]{1,80}", code)
    ):
        raise DomainError(code, "The previous test failed. Review configuration first", 502)
    raise delivery_unknown()


def _persist(descriptor: int, value: dict[str, str]) -> None:
    encoded = json.dumps(value, sort_keys=True).encode()
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.ftruncate(descriptor, 0)
    with os.fdopen(os.dup(descriptor), "wb") as destination:
        destination.write(encoded)
        destination.flush()
        os.fsync(destination.fileno())


def send_test(
    settings: Settings,
    request_id: UUID,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, str]:
    if not sandbox_configured(settings):
        raise DomainError(
            "gmail_not_configured", "Configure and authorize the Gmail sandbox first", 503
        )
    payload_hash = digest(
        {
            "client": settings.gmail_client_id,
            "sender": settings.gmail_sender_email,
            "recipient": settings.gmail_test_recipient,
            "subject": TEST_SUBJECT,
            "body": TEST_BODY,
        }
    )
    directory = Path(settings.gmail_token_path).parent / "requests"
    filename = f"{request_id}.json"
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                descriptor = os.open(
                    filename,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                return _replay(directory_fd, filename, payload_hash)
            try:
                # Persist admission before contacting Google. A crash leaves a
                # fail-closed marker because Gmail has no idempotency-key API.
                _persist(descriptor, {"payload_hash": payload_hash, "status": "started"})
                os.fsync(directory_fd)
                try:
                    result = GmailClient(settings, transport=transport).send_test_message(
                        request_id
                    )
                except DomainError as error:
                    if error.code != "gmail_delivery_unknown":
                        _persist(
                            descriptor,
                            {"payload_hash": payload_hash, "status": "failed", "code": error.code},
                        )
                    raise
                _persist(descriptor, {"payload_hash": payload_hash, **result})
                return result
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_fd)
    except OSError:
        raise DomainError(
            "gmail_journal_unavailable",
            "Gmail test journal is unavailable. Reconcile any prior test before sending",
            503,
        ) from None
