"""Commit Gmail reservations before provider calls and never repeat uncertain sends."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import Connection

from app.config import Settings
from app.db.connection import connect
from app.modules.audit.service import append_event
from app.modules.cases.repository import lock_case
from app.modules.compliance.repository import lock_contact_authority
from app.modules.conversations.dispatch import dispatch_message
from app.modules.conversations.schemas import DispatchRequest
from app.modules.email_demo import repository
from app.modules.email_demo.service import configured
from app.shared.caller import Caller
from app.shared.clock import utcnow
from app.shared.errors import DomainError

logger = logging.getLogger(__name__)


def authorize_delivery(
    connection: Connection[dict[str, Any]],
    settings: Settings,
    message: dict[str, Any],
    case: dict[str, Any],
    demo_id: str | None,
) -> dict[str, Any]:
    demo = repository.for_message(connection, message["id"])
    authority = lock_contact_authority(connection, message["account_id"])
    if (
        not demo
        or demo["id"] != demo_id
        or demo["reserved_at"] is None
        or demo["status"] != "running"
        or demo["run_id"] != message["run_id"]
        or demo["account_id"] != message["account_id"]
        or not authority
        or not authority["is_synthetic"]
    ):
        raise DomainError(
            "email_demo_forbidden", "A reserved synthetic email demo is required", 403
        )
    if (
        not configured(settings)
        or demo["sender_email"] != settings.gmail_sender_email
        or demo["recipient_email"] != settings.gmail_test_recipient
        or case["recipient_id"] != "email:" + demo["recipient_email"]
    ):
        raise DomainError("email_demo_binding_changed", "Email demo configuration has changed", 403)
    return demo


def reserve(
    settings: Settings, demo_id: str, caller_id: str, now: datetime
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    with connect(settings.database_url) as connection:
        # Match canonical evidence/account lock order, including parallel worker cycles.
        connection.execute("SELECT pg_advisory_xact_lock(71107002)")
        demo = repository.get_demo(connection, demo_id, caller_id)
        lock_case(connection, demo["account_id"])
        locked_demo = connection.execute(
            "SELECT d.*,r.status AS run_status,r.reason_codes AS run_reasons,r.outcome "
            "FROM collection_email_demos d JOIN collection_runs r ON r.id=d.run_id "
            "WHERE d.id=%s FOR UPDATE OF d",
            (demo_id,),
        ).fetchone()
        if not locked_demo:
            raise DomainError("not_found", "Email collection demo not found", 404)
        demo = locked_demo
        if demo["status"] not in {"queued", "running"}:
            return None
        if demo["reserved_at"] is not None:
            if demo["delivery_deadline_at"] <= now:
                repository.update(
                    connection, demo_id, "delivery_unknown", ["delivery_confirmation_pending"], now
                )
            return None
        run_state = demo["run_status"]
        if run_state in {"queued", "running"}:
            return None
        if run_state != "completed" or demo["outcome"] != "draft_created":
            state = "blocked" if run_state == "blocked" else "failed"
            reasons = demo["run_reasons"] or ["run_" + run_state]
            repository.update(connection, demo_id, state, reasons, now)
            return None
        message = connection.execute(
            "SELECT * FROM collection_messages WHERE id=%s FOR UPDATE", (demo["message_id"],)
        ).fetchone()
        if not message or message["channel"] != "email" or message["status"] != "draft":
            repository.update(connection, demo_id, "blocked", ["email_draft_missing"], now)
            return None
        repository.update(connection, demo_id, "running", [], now)
        connection.execute(
            "UPDATE collection_email_demos SET reserved_at=%s,delivery_deadline_at=%s WHERE id=%s",
            (now, now + timedelta(seconds=180), demo_id),
        )
        append_event(
            connection,
            demo["account_id"],
            "email_demo_dispatch_reserved",
            "backend",
            "delivery_unknown",
            now,
            demo["run_id"],
            correlation_id=message["id"],
        )
        return demo, message


def deliver(
    settings: Settings, demo_id: str, caller_id: str, clock: Callable[[], datetime]
) -> None:
    reservation = reserve(settings, demo_id, caller_id, clock())
    if not reservation:
        return
    demo, message = reservation
    try:
        with connect(settings.database_url) as connection:
            result = dispatch_message(
                connection,
                settings,
                message["id"],
                DispatchRequest(
                    idempotency_key=UUID(demo_id), expected_message_version=message["version"]
                ),
                Caller(account_id=demo["account_id"]),
                clock,
                email_demo_id=demo_id,
            )
            if result["status"] == "blocked":
                repository.update(connection, demo_id, "blocked", result["reason_codes"], clock())
    except DomainError as error:
        state = "delivery_unknown" if error.code == "gmail_delivery_unknown" else "failed"
        with connect(settings.database_url) as connection:
            lock_case(connection, demo["account_id"])
            repository.update(connection, demo_id, state, [error.code], clock())
            connection.execute(
                "UPDATE collection_messages SET status=%s,version=version+1 WHERE id=%s",
                (state, message["id"]),
            )
            append_event(
                connection,
                demo["account_id"],
                "email_demo_dispatch",
                "backend",
                state,
                clock(),
                demo["run_id"],
                [error.code],
                correlation_id=message["id"],
            )
    except Exception:  # noqa: BLE001 - reservation survives provider/commit ambiguity
        logger.error("Email demo delivery unconfirmed demo_id=%s", demo_id)


def tick(settings: Settings, now: datetime | None = None) -> None:
    clock: Callable[[], datetime] = utcnow
    if now is not None:
        fixed_now = now
        clock = lambda: fixed_now
    with connect(settings.database_url) as connection:
        pending = repository.pending(connection, clock())
    for row in pending:
        deliver(settings, row["id"], row["caller_id"], clock)
