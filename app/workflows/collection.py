"""Business workflow: runtime completion is accepted only after persisted evidence validates."""

from datetime import timedelta
from uuid import uuid4

from app.db.connection import connect
from app.modules.collection.service import CollectionService
from app.modules.conversations.schemas import DraftCandidate
from app.modules.knowledge_context.contracts import KnowledgeQuery
from app.modules.runs.repository import add_step, finish, verify_fence
from app.modules.runs.schemas import ArtifactCandidate
from app.shared.caller import Caller
from app.shared.errors import DomainError


def prepare_knowledge(settings, run, now, caller, clock=None):
    service = CollectionService(settings, clock or (lambda: now))
    task = run["payload"]["task_type"]
    return service.retrieve_approved_context(
        run["id"],
        KnowledgeQuery(
            query=run["payload"]["instruction"][:1000],
            purpose={
                "follow_up": "collection_draft",
                "collector_brief": "copilot",
                "improvement_review": "improvement_review",
            }[task],
            deadline_at=now + timedelta(seconds=30),
        ),
        caller,
    )


def execute_simulated(settings, run, attempt, fence, now, snapshot):
    caller = Caller(
        principal_id="local-worker",
        actor_kind="worker",
        account_id=run["account_id"],
        run_id=run["id"],
        execution_attempt_id=attempt,
        fence=fence,
    )
    service = CollectionService(settings, lambda: now)
    with connect(settings.database_url) as connection:
        job = verify_fence(connection, run["id"], attempt, fence, now)
        if job["cancellation_requested_at"]:
            finish(connection, run["id"], "cancelled", None, "Cancelled before generation", now)
            return
        add_step(connection, run["id"], "manager", "Manager accepted account task (simulated)", now)
        add_step(connection, run["id"], "knowledge", "Approved demo fixture retrieved", now)
    citations = [s["citation_id"] for s in snapshot["sources"]]
    scenario = run["payload"].get("demo_scenario")
    if scenario in ("hardship", "dispute", "human_request"):
        service.request_human_review(
            run["account_id"],
            {"reason": scenario, "run_id": run["id"], "idempotency_key": str(uuid4())},
            caller,
        )
        with connect(settings.database_url) as connection:
            verify_fence(connection, run["id"], attempt, fence, now)
            finish(
                connection,
                run["id"],
                "completed",
                "escalated",
                "Kasus diteruskan kepada petugas manusia; kontak dijeda.",
                now,
            )
        return
    task = run["payload"]["task_type"]
    if task == "follow_up":
        message = service.submit_draft_candidate(
            DraftCandidate(
                run_id=run["id"],
                account_id=run["account_id"],
                execution_attempt_id=attempt,
                text_with_placeholders="Selamat siang {borrower_name}. Kami ingin mengingatkan "
                "tagihan {loan_reference} sebesar {outstanding_amount} yang jatuh tempo pada "
                "{due_date}. Jika membutuhkan bantuan, silakan beri tahu kami. Terima kasih.",
                citation_ids=citations,
                idempotency_key=uuid4(),
            ),
            caller,
        )
        service.submit_run_artifact(
            ArtifactCandidate(
                run_id=run["id"],
                account_id=run["account_id"],
                execution_attempt_id=attempt,
                kind="qa_report",
                text="Fixture QA: bahasa sopan, fakta "
                "dirender backend, citation tersedia. Keputusan dispatch tetap di backend.",
                citation_ids=citations,
                idempotency_key=uuid4(),
            ),
            caller,
        )
        with connect(settings.database_url) as connection:
            verify_fence(connection, run["id"], attempt, fence, now)
            add_step(
                connection,
                run["id"],
                "collection",
                "Candidate saved with backend-rendered facts",
                now,
            )
            add_step(connection, run["id"], "qa", "Advisory QA report saved (demo fixture)", now)
            status = "completed" if message["status"] == "draft" else "blocked"
            finish(
                connection,
                run["id"],
                status,
                "draft_created",
                "Draft pengingat tersimpan untuk dispatch simulasi."
                if status == "completed"
                else "Draft ditahan oleh gate backend.",
                now,
                [] if status == "completed" else ["guardrail_rejected"],
            )
    else:
        kind = "collector_brief" if task == "collector_brief" else "improvement_proposal"
        text = (
            "Brief collector: tinjau fakta akun dan SOP; utamakan bantuan serta penanganan "
            "manusia untuk kesulitan bayar."
            if task == "collector_brief"
            else "Usulan: tinjau metrik kontak dan perjelas bantuan kesulitan bayar. Perubahan "
            "kebijakan memerlukan persetujuan manusia dan tidak diterapkan otomatis."
        )
        service.submit_run_artifact(
            ArtifactCandidate(
                run_id=run["id"],
                account_id=run["account_id"],
                execution_attempt_id=attempt,
                kind=kind,
                text=text,
                citation_ids=citations,
                idempotency_key=uuid4(),
            ),
            caller,
        )
        with connect(settings.database_url) as connection:
            verify_fence(connection, run["id"], attempt, fence, now)
            add_step(
                connection,
                run["id"],
                "copilot" if task == "collector_brief" else "improvement",
                "Advisory artifact saved (simulated)",
                now,
            )
            finish(
                connection,
                run["id"],
                "completed",
                "brief_created" if task == "collector_brief" else "proposal_created",
                text,
                now,
            )


def validate_final(connection, run, result):
    if result.application_run_id != run["id"] or result.task_type != run["payload"]["task_type"]:
        raise DomainError("result_invalid", "Final result does not match run")
    from app.modules.knowledge_context.service import validate_citations

    validate_citations(connection, run["id"], result.citation_ids)
    artifacts = connection.execute(
        "SELECT * FROM run_artifacts WHERE run_id=%s", (run["id"],)
    ).fetchall()
    ids = {a["id"] for a in artifacts}
    if not result.artifact_ids or not set(result.artifact_ids) <= ids:
        raise DomainError("result_invalid", "Final result lacks persisted artifacts")
    kind = {
        "follow_up": "qa_report",
        "collector_brief": "collector_brief",
        "improvement_review": "improvement_proposal",
    }[result.task_type]
    if not any(a["kind"] == kind and a["id"] in result.artifact_ids for a in artifacts):
        raise DomainError("result_invalid", "Required specialist artifact is missing")
    if result.task_type == "follow_up":
        message = connection.execute(
            "SELECT * FROM collection_messages WHERE id=%s AND run_id=%s",
            (result.candidate_message_id, run["id"]),
        ).fetchone()
        if not message:
            raise DomainError("result_invalid", "Final candidate reference is invalid")
        return "blocked", "draft_created", ["guardrail_unavailable"]
    return (
        "completed",
        ("brief_created" if result.task_type == "collector_brief" else "proposal_created"),
        [],
    )
