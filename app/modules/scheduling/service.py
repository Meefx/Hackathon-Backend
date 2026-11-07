"""Server-owned daily admission and restart-safe simulated delivery."""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from psycopg import Error as DatabaseError

from app.config import Settings
from app.db.connection import connect
from app.modules.compliance.service import evaluate_contact_policy
from app.modules.conversations.dispatch import dispatch_message
from app.modules.conversations.schemas import DispatchRequest
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run_in_transaction
from app.modules.scheduling import repository
from app.modules.scheduling.schemas import AutomationStatus
from app.shared.caller import Caller
from app.shared.clock import utcnow
from app.shared.errors import DomainError
from app.shared.idempotency import digest

logger = logging.getLogger(__name__)

SCHEDULER_PRINCIPAL = "daily-collection-scheduler"
TIMEZONE = "Asia/Jakarta"
TEMPORARY_REASONS = {
    "sunday",
    "holiday",
    "outside_hours",
    "outside_contact_window",
    "frequency_cap_daily",
    "frequency_cap_weekly",
    "frequency_limit",
    "account_run_active",
}


def classify(reasons):
    return "deferred" if reasons and set(reasons) <= TEMPORARY_REASONS else "needs_attention"


def reconcile_deliveries(connection, settings, clock):
    now = clock()
    for item in repository.pending_deliveries(connection):
        run_status = item["run_status"]
        if run_status in {"queued", "running"}:
            repository.update_item(connection, item["id"], run_status, [], now)
            continue
        if run_status == "blocked" and set(item["run_reasons"]) <= TEMPORARY_REASONS:
            retry_pre_generation(connection, item, now)
            continue
        if run_status != "completed" or item["outcome"] != "draft_created":
            reasons = item["run_reasons"] or ["run_" + run_status]
            repository.update_item(connection, item["id"], classify(reasons), reasons, now)
            continue
        message = repository.outbound_message(connection, item["run_id"])
        if not message:
            repository.update_item(
                connection, item["id"], "needs_attention", ["draft_missing"], now
            )
            continue
        receipt = repository.receipt_for_message(connection, message["id"])
        if receipt:
            repository.update_item(connection, item["id"], "sent", [], now)
            continue
        if item["state"] == "needs_attention":
            # Failed delivery needs explicit revalidation. Keep its actionable reasons.
            continue
        caller = Caller(
            principal_id=SCHEDULER_PRINCIPAL,
            actor_kind="scheduler",
            account_id=item["account_id"],
            run_id=item["run_id"],
        )
        try:
            # Delivery, receipt, audit, idempotency and ledger commit together.
            with connection.transaction():
                result = dispatch_message(
                    connection,
                    settings,
                    message["id"],
                    DispatchRequest(
                        idempotency_key=uuid5(NAMESPACE_URL, item["id"] + ":delivery"),
                        expected_message_version=message["version"],
                    ),
                    caller,
                    clock,
                )
        except DomainError as error:
            repository.update_item(connection, item["id"], "needs_attention", [error.code], now)
            continue
        reasons = result["reason_codes"]
        if result["status"] == "simulated_sent":
            state = "sent"
        else:
            state = "needs_attention"
            if classify(reasons) == "deferred":
                reasons = [*reasons, "draft_revalidation_required"]
        repository.update_item(connection, item["id"], state, reasons, now)


def reconcile_attention(connection, now):
    """Close superseded exceptions while retaining their original reasons."""
    items = repository.attention_candidates(connection)
    today = now.astimezone(ZoneInfo(TIMEZONE)).date()
    for item in items:
        if item["run_status"] == "interrupted":
            continue
        paid_or_current = item["outstanding_amount"] <= 0 or item["due_date"] >= today
        resolved = paid_or_current
        if not resolved and not item["run_id"]:
            decision = evaluate_contact_policy(connection, item["account_id"], now)
            resolved = set(decision["reason_codes"]) <= TEMPORARY_REASONS
        if not resolved and item["run_id"]:
            resolved = bool(
                repository.later_receipt(connection, item["account_id"], item["created_at"])
            )
        if resolved:
            repository.update_item(connection, item["id"], "resolved", item["reason_codes"], now)


def retry_pre_generation(connection, item, now):
    """Only retry a conclusively blocked task that never produced domain effects."""
    effects = repository.run_has_effects(connection, item["run_id"])
    if effects or item["business_date"] != now.astimezone(ZoneInfo(TIMEZONE)).date():
        repository.update_item(connection, item["id"], "deferred", item["run_reasons"], now)
        return
    decision = evaluate_contact_policy(connection, item["account_id"], now)
    reasons = decision["reason_codes"]
    active = repository.active_run(connection, item["account_id"], item["run_id"])
    if active:
        reasons = [
            *reasons,
            "runtime_interrupted" if active["status"] == "interrupted" else "account_run_active",
        ]
    if reasons:
        repository.update_item(connection, item["id"], classify(reasons), reasons, now)
        return
    updated = repository.requeue_run(connection, item["run_id"])
    if not updated:
        repository.update_item(connection, item["id"], "needs_attention", ["run_cancelled"], now)
        return
    repository.update_item(connection, item["id"], "queued", [], now)


def admit_account(connection, settings, account_id, today, now):
    item_id = str(uuid5(NAMESPACE_URL, f"collectflow:daily:{today}:{account_id}"))
    item = repository.ensure_item(connection, item_id, today, account_id, now)
    if item["run_id"]:
        return
    decision = evaluate_contact_policy(connection, account_id, now)
    reasons = decision["reason_codes"]
    active = repository.active_run(connection, account_id)
    if active:
        reasons = [
            *reasons,
            "runtime_interrupted" if active["status"] == "interrupted" else "account_run_active",
        ]
    if reasons:
        state = classify(reasons)
        if set(reasons) == {"case_paused"} and decision["contact_state"] == "paused_human_review":
            state = "deferred"
        repository.update_item(connection, item_id, state, reasons, now)
        return
    session = repository.manager_session(connection, account_id, SCHEDULER_PRINCIPAL)
    request = StartRunRequest(
        instruction="Jalankan collection harian untuk akun ini. Manager mengoordinasikan "
        "knowledge, collection, dan QA. Patuhi seluruh gate backend dan eskalasikan "
        "kasus yang memerlukan pertimbangan manusia.",
        runtime_mode=settings.scheduler_runtime_mode,
        idempotency_key=uuid5(NAMESPACE_URL, item_id + ":run"),
        manager_session_id=session["id"] if session else None,
    )
    facts = repository.account_facts(connection, account_id)
    accepted = start_run_in_transaction(
        connection,
        settings,
        account_id,
        request,
        Caller(principal_id=SCHEDULER_PRINCIPAL, actor_kind="scheduler"),
        now,
    )
    repository.link_run(connection, item_id, accepted["run_id"], digest(facts), now)


def tick(settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    """Reconcile admitted deliveries and scan today's overdue portfolio after 09.00 WIB."""
    if not settings.scheduler_enabled:
        return {"scanned": False}
    clock: Callable[[], datetime] = utcnow
    if now is not None:
        fixed_now = now
        clock = lambda: fixed_now
    with connect(settings.database_url) as connection:
        # Serializes scheduler replicas. Knowledge lock order matches canonical delivery.
        repository.lock_scheduler(connection)
        reconcile_attention(connection, clock())
        reconcile_deliveries(connection, settings, clock)
        reconcile_attention(connection, clock())
        now = clock()
        local = now.astimezone(ZoneInfo(TIMEZONE))
        if local.hour < settings.scheduler_start_hour:
            return {"scanned": False}
        for account in repository.candidates(connection, local.date()):
            try:
                with connection.transaction():
                    admit_account(connection, settings, account["id"], local.date(), clock())
            except (DomainError, DatabaseError) as error:
                logger.error(
                    "Scheduler admission failed account_id=%s error_type=%s",
                    account["id"],
                    type(error).__name__,
                )
                reason = error.code if isinstance(error, DomainError) else "admission_failed"
                item_id = str(
                    uuid5(NAMESPACE_URL, f"collectflow:daily:{local.date()}:{account['id']}")
                )
                repository.ensure_item(connection, item_id, local.date(), account["id"], now)
                repository.update_item(connection, item_id, "needs_attention", [reason], now)
        repository.record_scan(connection, now)
    return {"scanned": True}


def status(settings: Settings, now: datetime) -> dict[str, Any]:
    today = now.astimezone(ZoneInfo(TIMEZONE)).date()
    with connect(settings.database_url) as connection:
        rows, attention, last = repository.status_rows(connection, today)
    return AutomationStatus.model_validate(
        {
            "enabled": settings.scheduler_enabled,
            "daily_start_time": f"{settings.scheduler_start_hour:02d}:00",
            "timezone": TIMEZONE,
            "runtime_mode": settings.scheduler_runtime_mode,
            "business_date": today,
            "last_scan_at": last["last_scan_at"] if last else None,
            "counts": {row["state"]: row["n"] for row in rows},
            "attention_items": attention,
        }
    ).model_dump(mode="json")
