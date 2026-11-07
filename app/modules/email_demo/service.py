"""One intentional click admits one synthetic case and its normal collection run."""

from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.config import Settings
from app.db.connection import connect
from app.integrations.gmail.client import sandbox_configured
from app.modules.email_demo import repository
from app.modules.email_demo.schemas import EmailDemo, EmailDemoRequest
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run_in_transaction
from app.shared.caller import Caller
from app.shared.clock import utcnow
from app.shared.errors import DomainError


def require_operator(caller: Caller) -> None:
    if caller.actor_kind != "operator" or caller.principal_id != "local-operator":
        raise DomainError("dispatch_forbidden", "Local operator permission is required", 403)


def configured(settings: Settings) -> bool:
    return settings.demo_fixture_enabled and sandbox_configured(settings)


def masked_recipient(recipient: str) -> str:
    local, domain = recipient.rsplit("@", 1)
    return local[:1] + "***@" + domain


def snapshot(row: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    state = row["status"]
    reasons = row["reason_codes"]
    if state == "running" and row["reserved_at"] and row["delivery_deadline_at"] <= now:
        state, reasons = "delivery_unknown", ["delivery_confirmation_pending"]
    if state in {"queued", "running"}:
        run_state = row["run_status"]
        if run_state == "blocked":
            state, reasons = "blocked", row["run_reasons"]
        elif run_state in {"failed", "cancelled", "interrupted"}:
            state, reasons = "failed", row["run_reasons"] or ["run_" + run_state]
        elif run_state in {"running", "completed"}:
            state = "running"
    return EmailDemo.model_validate(
        {
            **row,
            "status": state,
            "reason_codes": reasons,
            "recipient_masked": masked_recipient(row["recipient_email"]),
        }
    ).model_dump(mode="json")


def get_demo(
    settings: Settings, demo_id: str, caller: Caller, now: datetime | None = None
) -> dict[str, Any]:
    require_operator(caller)
    with connect(settings.database_url) as connection:
        return snapshot(repository.get_demo(connection, demo_id, caller.principal_id), now)


def status(settings: Settings, caller: Caller, now: datetime | None = None) -> dict[str, Any]:
    require_operator(caller)
    available = configured(settings)
    with connect(settings.database_url) as connection:
        row = repository.latest(connection, caller.principal_id)
    return {
        "configured": available,
        "recipient_masked": masked_recipient(settings.gmail_test_recipient) if available else None,
        "latest": snapshot(row, now) if row else None,
    }


def start_demo(
    settings: Settings, request: EmailDemoRequest, caller: Caller, now: datetime
) -> dict[str, Any]:
    require_operator(caller)
    key = str(request.idempotency_key)
    with connect(settings.database_url) as connection:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("email-demo:" + key,)
        )
        previous = connection.execute(
            "SELECT id FROM collection_email_demos WHERE request_key=%s AND caller_id=%s",
            (key, caller.principal_id),
        ).fetchone()
        if previous:
            return snapshot(
                repository.get_demo(connection, previous["id"], caller.principal_id), now
            )
        if not configured(settings):
            raise DomainError(
                "gmail_not_configured", "Configure and authorize the Gmail demo first", 503
            )
        demo_id = str(uuid4())
        account_id = "EMAIL-DEMO-" + demo_id
        today = now.astimezone(ZoneInfo("Asia/Jakarta")).date()
        connection.execute(
            "INSERT INTO accounts(id,borrower_name,loan_reference,outstanding_amount,due_date,assigned_to) "
            "VALUES (%s,'Peminjam Demo Email',%s,250000,%s,'Demo Email')",
            (account_id, "DEMO-" + demo_id, today),
        )
        connection.execute(
            "INSERT INTO collection_cases(account_id,recipient_id,recipient_verified,eligible) "
            "VALUES (%s,%s,TRUE,TRUE)",
            (account_id, "email:" + settings.gmail_test_recipient),
        )
        connection.execute(
            "INSERT INTO collection_authorities(account_id,principal,product,borrower_ref,"
            "borrower_timezone,borrower_owned,verified,opted_out,is_synthetic,data_verified_at,"
            "mandate_active,calendar_years,holidays) "
            "VALUES (%s,'cf','spaylater',%s,'Asia/Jakarta',TRUE,TRUE,FALSE,TRUE,%s,TRUE,%s,'[]')",
            (
                account_id,
                uuid5(NAMESPACE_URL, account_id),
                now,
                Jsonb([today.year, today.year + 1]),
            ),
        )
        accepted = start_run_in_transaction(
            connection,
            settings,
            account_id,
            StartRunRequest(
                instruction="Siapkan pengingat tagihan sintetis yang sopan untuk demo email. "
                "Gunakan fakta akun dan knowledge approved yang tersedia.",
                runtime_mode="simulated",
                idempotency_key=request.idempotency_key,
            ),
            caller,
            now,
        )
        connection.execute(
            "INSERT INTO collection_email_demos(id,request_key,caller_id,account_id,run_id,"
            "sender_email,recipient_email,status,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s)",
            (
                demo_id,
                key,
                caller.principal_id,
                account_id,
                accepted["run_id"],
                settings.gmail_sender_email,
                settings.gmail_test_recipient,
                now,
                now,
            ),
        )
        return snapshot(repository.get_demo(connection, demo_id, caller.principal_id), now)


def is_demo_run(connection: Connection[dict[str, Any]], run: dict[str, Any]) -> bool:
    demo = repository.for_run(connection, run["id"])
    if demo and (
        demo["account_id"] != run["account_id"]
        or demo["caller_id"] != run["caller_id"]
        or run["requested_runtime_mode"] != "simulated"
    ):
        raise DomainError("email_demo_forbidden", "Email demo run binding is invalid", 403)
    return demo is not None


def register_draft(
    connection: Connection[dict[str, Any]], run_id: str, message_id: str, now: datetime
) -> None:
    repository.attach_message(connection, run_id, message_id, now)


def record_submission(
    connection: Connection[dict[str, Any]], demo_id: str, provider_id: str, now: datetime
) -> None:
    repository.update(connection, demo_id, "submitted", [], now, provider_id)
