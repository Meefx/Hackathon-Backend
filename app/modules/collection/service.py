"""Explicit public service boundary shared by REST, MCP, and worker adapters."""

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.config import Settings, get_settings
from app.db.connection import connect
from app.integrations.knowledge.gateway import get_gateway
from app.modules.accounts.service import get_account
from app.modules.compliance.service import evaluate_contact_policy
from app.modules.conversations import service as conversations
from app.modules.conversations.schemas import DraftCandidate
from app.modules.knowledge_context.contracts import KnowledgeQuery
from app.modules.knowledge_context.service import persist_snapshot, validate_citations
from app.modules.reviews import service as reviews
from app.modules.runs.repository import check_assignment, require_run
from app.modules.runs.schemas import ArtifactCandidate
from app.shared.caller import Caller
from app.shared.clock import utcnow
from app.shared.errors import DomainError
from app.shared.idempotency import record, replay

LOCAL_OPERATOR = Caller()


class CollectionService:
    def __init__(
        self, settings: Settings | None = None, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.clock = clock or utcnow

    def authorize(
        self, connection: Connection, caller: Caller, account_id: str, run_id: str | None = None
    ) -> None:
        check_assignment(connection, caller, account_id, run_id or caller.run_id, self.clock())

    def get_account(self, account_id: str, caller: Caller = LOCAL_OPERATOR) -> dict:
        with connect(self.settings.database_url) as connection:
            self.authorize(connection, caller, account_id)
        result = get_account(
            self.settings.database_url,
            account_id,
            self.clock().astimezone(ZoneInfo("Asia/Jakarta")).date(),
        )
        if not result:
            raise DomainError("not_found", "Account not found", 404)
        return result

    def get_contact_policy(self, account_id: str, caller: Caller = LOCAL_OPERATOR) -> dict:
        with connect(self.settings.database_url) as connection:
            self.authorize(connection, caller, account_id)
            return evaluate_contact_policy(connection, account_id, self.clock())

    def get_contact_history(
        self, account_id: str, caller: Caller = LOCAL_OPERATOR, limit: int = 20, offset: int = 0
    ) -> dict:
        with connect(self.settings.database_url) as connection:
            self.authorize(connection, caller, account_id)
            items = connection.execute(
                "SELECT * FROM collection_messages WHERE account_id=%s "
                "ORDER BY created_at,id LIMIT %s OFFSET %s",
                (account_id, min(limit, 100), offset),
            ).fetchall()
            total = connection.execute(
                "SELECT count(*) AS n FROM collection_messages WHERE account_id=%s", (account_id,)
            ).fetchone()["n"]
            return {
                "items": [conversations.message_json(r) for r in items],
                "total": total,
                "limit": min(limit, 100),
                "offset": offset,
            }

    def retrieve_approved_context(
        self, run_id: str, request: KnowledgeQuery | dict, caller: Caller = LOCAL_OPERATOR
    ) -> dict:
        if isinstance(request, dict):
            request = KnowledgeQuery.model_validate(request)
        with connect(self.settings.database_url) as connection:
            run = require_run(connection, run_id)
            self.authorize(connection, caller, run["account_id"], run_id)
        try:
            result = get_gateway(self.settings).query_approved(request, caller)
        except Exception:  # noqa: BLE001 - dependency failures must fail closed
            from app.modules.knowledge_context.contracts import KnowledgeResult

            result = KnowledgeResult(
                status="unavailable",
                source_mode="knowledge_module",
                reason_code="knowledge_unavailable",
            )
        with connect(self.settings.database_url) as connection:
            self.authorize(connection, caller, run["account_id"], run_id)
            return persist_snapshot(connection, run_id, result, self.clock())

    def submit_draft_candidate(
        self, candidate: DraftCandidate | dict, caller: Caller = LOCAL_OPERATOR
    ) -> dict:
        candidate = DraftCandidate.model_validate(candidate)
        return conversations.submit_draft(self.settings, candidate, caller, self.clock())

    def submit_run_artifact(
        self, candidate: ArtifactCandidate | dict, caller: Caller = LOCAL_OPERATOR
    ) -> dict:
        candidate = ArtifactCandidate.model_validate(candidate)
        with connect(self.settings.database_url) as connection:
            run = require_run(connection, candidate.run_id, True)
            payload = candidate.model_dump(mode="json")
            scope = "artifact:" + run["id"]
            if old := replay(connection, scope, str(candidate.idempotency_key), payload):
                check_assignment(
                    connection,
                    caller,
                    candidate.account_id,
                    candidate.run_id,
                    self.clock(),
                    allow_inactive=True,
                )
                return old
            self.authorize(connection, caller, candidate.account_id, candidate.run_id)
            job = connection.execute(
                "SELECT * FROM collection_jobs WHERE run_id=%s", (run["id"],)
            ).fetchone()
            if (
                run["account_id"] != candidate.account_id
                or run["status"] != "running"
                or job["execution_attempt_id"] != candidate.execution_attempt_id
            ):
                raise DomainError("assignment_denied", "Active execution required", 403)
            expected = {
                "follow_up": "qa_report",
                "collector_brief": "collector_brief",
                "improvement_review": "improvement_proposal",
            }[run["payload"]["task_type"]]
            if candidate.kind != expected:
                raise DomainError("result_invalid", "Artifact kind does not match task")
            validate_citations(connection, run["id"], candidate.citation_ids)
            artifact = {
                "id": str(uuid4()),
                "run_id": run["id"],
                "kind": candidate.kind,
                "text": candidate.text,
                "message_id": None,
                "citation_ids": candidate.citation_ids,
                "review_state": "pending" if candidate.kind == "improvement_proposal" else None,
                "created_at": self.clock().isoformat(),
            }
            connection.execute(
                "INSERT INTO run_artifacts VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    artifact["id"],
                    run["id"],
                    candidate.kind,
                    candidate.text,
                    None,
                    Jsonb(candidate.citation_ids),
                    artifact["review_state"],
                    self.clock(),
                ),
            )
            record(connection, scope, str(candidate.idempotency_key), payload, artifact)
            return artifact

    def request_human_review(
        self, account_id: str, request: dict, caller: Caller = LOCAL_OPERATOR
    ) -> dict:
        request = request if isinstance(request, dict) else request.model_dump(mode="json")
        reason = request.get("reason", "human_request")
        if reason not in ("hardship", "dispute", "human_request"):
            raise DomainError("invalid_reason", "Review reason is invalid", 422)
        with connect(self.settings.database_url) as connection:
            payload = {
                "reason": reason,
                "run_id": request.get("run_id"),
                "note": request.get("note"),
            }
            scope = "review_request:" + account_id
            key = str(request["idempotency_key"])
            if old := replay(connection, scope, key, payload):
                check_assignment(
                    connection,
                    caller,
                    account_id,
                    request.get("run_id"),
                    self.clock(),
                    allow_inactive=True,
                )
                return old
            self.authorize(connection, caller, account_id, request.get("run_id"))
            review_id = reviews.open_review(
                connection, account_id, reason, self.clock(), request.get("run_id")
            )
            result = dict(
                connection.execute(
                    "SELECT * FROM collection_reviews WHERE id=%s", (review_id,)
                ).fetchone()
            )
            from app.modules.reviews.schemas import ReviewItem

            result = ReviewItem.model_validate(result).model_dump(mode="json")
            record(connection, scope, key, payload, result)
            return result
