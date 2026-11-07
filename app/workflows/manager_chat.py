"""Portfolio operator conversation on the existing durable run infrastructure."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.db.connection import connect
from app.modules.manager.chat_service import ManagerChatService
from app.modules.runs.repository import add_step, finish, verify_fence
from app.shared.caller import Caller
from app.shared.errors import DomainError


def worker_caller(run: dict, attempt: str, fence: int) -> Caller:
    return Caller(
        principal_id="local-worker",
        actor_kind="worker",
        account_id=None,
        run_id=run["id"],
        execution_attempt_id=attempt,
        fence=fence,
    )


def prepare_context(
    settings: Settings, run: dict, attempt: str, fence: int, now: datetime
) -> dict[str, Any]:
    return ManagerChatService(settings, lambda: now).context_for_run(
        run["id"], worker_caller(run, attempt, fence)
    )


def simulated_reply(context: dict[str, Any]) -> str:
    overview = context["overview"]
    lines = [
        "Simulated Manager analysis",
        (
            f"The portfolio contains {overview['total_accounts']} accounts with "
            f"Rp{overview['total_outstanding']:,} outstanding. "
            f"{overview['overdue_accounts']} accounts are overdue, totaling "
            f"Rp{overview['overdue_amount']:,}."
        ),
    ]
    selected = context["selected_account"]
    if selected:
        account = selected["account"]
        policy = selected["policy"]
        lines.append(
            f"Selected account {account['id']} has Rp{account['outstanding_amount']:,} "
            f"outstanding and is {account['days_past_due']} days past due. "
            f"Contact state {policy['contact_state']}."
        )
        if policy["reason_codes"]:
            lines.append("Backend contact decision. " + ", ".join(policy["reason_codes"]))
        if selected["open_reviews"]:
            lines.append(
                "Open human reviews. "
                + ", ".join(review["reason"] for review in selected["open_reviews"][:5])
            )
        lines.append("Account task cards below are available for an operator decision.")
    else:
        lines.append(
            "Choose an account from the portfolio to inspect its contact decision and human reviews."
        )
    lines.append(
        "This simulator displays current backend facts. Select Hermes for free-form agent reasoning."
    )
    knowledge = context.get("knowledge", {})
    sources = knowledge.get("sources", [])
    if sources:
        lines.append(
            "Approved knowledge references. "
            + "; ".join(f"{source['title']} [{source['citation_id']}]" for source in sources)
        )
    else:
        lines.append(
            "No approved knowledge was retrieved for this scope; legal answers need review."
        )
    return "\n\n".join(lines)


def execute_simulated_chat(
    settings: Settings, run: dict, attempt: str, fence: int, now: datetime, context: dict[str, Any]
) -> None:
    caller = worker_caller(run, attempt, fence)
    service = ManagerChatService(settings, lambda: now)
    text = simulated_reply(context)
    service.submit_response(run["id"], text, caller, uuid4())
    selected = context["selected_account"]
    if selected:
        for task, instruction in (
            (
                "collector_brief",
                "Analyze this account and prepare a brief for its human collector.",
            ),
            (
                "follow_up",
                "Prepare a respectful collection reminder using approved account facts and SOP.",
            ),
            (
                "improvement_review",
                "Review this account's collection process and propose improvements.",
            ),
        ):
            service.propose_task(
                run["id"], selected["account"]["id"], task, instruction, caller, uuid4()
            )
    with connect(settings.database_url) as connection:
        job = verify_fence(connection, run["id"], attempt, fence, now)
        if job["cancellation_requested_at"]:
            finish(connection, run["id"], "cancelled", None, "Chat cancelled", now)
            return
        add_step(
            connection, run["id"], "manager", "Portfolio and case facts analyzed (simulated)", now
        )
        finish(connection, run["id"], "completed", "manager_replied", text, now)


def validate_chat_result(connection, run: dict, result) -> tuple[str, str, list[str]]:
    if result.candidate_message_id or result.citation_ids or result.review_requested:
        raise DomainError("result_invalid", "Portfolio chat requires an advisory response")
    artifacts = connection.execute(
        "SELECT id,kind,text FROM run_artifacts WHERE run_id=%s", (run["id"],)
    ).fetchall()
    ids = {artifact["id"] for artifact in artifacts}
    if not result.artifact_ids or not set(result.artifact_ids) <= ids:
        raise DomainError("result_invalid", "Chat result lacks its saved response")
    if not any(
        artifact["id"] in result.artifact_ids
        and artifact["kind"] == "manager_response"
        and artifact["text"] == result.summary
        for artifact in artifacts
    ):
        raise DomainError("result_invalid", "Chat summary must match its saved response")
    return "completed", "manager_replied", []
