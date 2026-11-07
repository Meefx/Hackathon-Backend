"""Reusable trust use cases, each owning one atomic PostgreSQL transaction.

REST and future adapters call this service. The existing deterministic knowledge,
runtime and dispatch engines retain their single active implementations.
"""

import hashlib
from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID

from psycopg import Connection
from psycopg_pool import ConnectionPool
from pydantic import TypeAdapter

from app.core.clock import Clock
from app.db.connection import check_ready
from app.modules.trust import repository
from app.trust import knowledge, runtime
from app.trust.audit import append_event, canonical, verify_chain
from app.trust.errors import TrustError
from app.trust.pipeline import execute
from app.trust.security import Actor, issue_token, require
from app.trust.settings import TrustSettings

JSON_VALUE: TypeAdapter[Any] = TypeAdapter(Any)


class Classifier(Protocol):
    profile: str

    def evaluate(self, text: str) -> dict[str, Any]: ...


class TrustService:
    def __init__(
        self,
        settings: TrustSettings,
        database: ConnectionPool[Connection[dict[str, Any]]],
        clock: Clock,
        classifier: Classifier,
    ) -> None:
        self.settings = settings
        self.database = database
        self.clock = clock
        self.classifier = classifier

    def ready(self) -> dict[str, Any]:
        if not check_ready(self.settings.database_url):
            raise TrustError("DATABASE_NOT_READY", 503)
        return {
            "ready": True,
            "simulation_mode": True,
            "classifier_profile": self.classifier.profile,
            "semantic_embeddings": False,
        }

    def demo_login(self, role: str, principal: str) -> dict[str, Any]:
        if not self.settings.demo_auth_enabled:
            raise TrustError("DEMO_AUTH_DISABLED", 404)
        who = Actor(
            f"demo-{role}", role, principal, "spaylater" if principal == "cf" else "spinjam"
        )
        return {
            "access_token": issue_token(who, self.settings.jwt_secret, self.clock.now()),
            "token_type": "bearer",
            "simulation_mode": True,
            "warning": "Demo identity selection only. No production authentication.",
        }

    def _write(
        self,
        who: Actor,
        key: UUID,
        route: str,
        capability: str,
        payload: dict[str, Any],
        action: Callable[[Connection[dict[str, Any]]], dict[str, Any]],
    ) -> dict[str, Any]:
        # Authorization precedes replay, including callers sharing a subject ID.
        require(who, capability)
        request_hash = hashlib.sha256(canonical(payload).encode()).hexdigest()
        with self.database.connection() as conn:
            repository.lock_write(conn, who, key, route)
            previous = repository.previous_request(conn, who, key, route)
            if previous:
                if previous["request_hash"] != request_hash or previous["actor_id"] != who.subject:
                    raise TrustError("IDEMPOTENCY_MISMATCH")
                return previous["response"]
            result = JSON_VALUE.dump_python(action(conn), mode="json")
            repository.save_request(conn, who, key, route, request_hash, result)
            return result

    def ingest(self, who: Actor, key: UUID, data: dict[str, Any]) -> dict[str, Any]:
        return self._write(
            who,
            key,
            "ingest",
            "knowledge:ingest",
            data,
            lambda conn: knowledge.ingest(conn, who, data, self.clock.now()),
        )

    def documents(self, who: Actor, limit: int, offset: int) -> list[dict]:
        with self.database.connection() as conn:
            return knowledge.list_documents(conn, who, limit=limit, offset=offset)

    def qa_document(self, who: Actor, key: UUID, document_id: UUID) -> dict[str, Any]:
        return self._write(
            who,
            key,
            f"document/{document_id}/qa",
            "knowledge:qa",
            {},
            lambda conn: knowledge.qa_review(conn, who, str(document_id), self.clock.now()),
        )

    def approve_document(
        self, who: Actor, key: UUID, document_id: UUID, note: str
    ) -> dict[str, Any]:
        return self._write(
            who,
            key,
            f"document/{document_id}/approve",
            "knowledge:approve",
            {"note": note},
            lambda conn: knowledge.approve(conn, who, str(document_id), note, self.clock.now()),
        )

    def retire_document(
        self, who: Actor, key: UUID, document_id: UUID, note: str
    ) -> dict[str, Any]:
        return self._write(
            who,
            key,
            f"document/{document_id}/retire",
            "knowledge:retire",
            {"note": note},
            lambda conn: knowledge.retire(conn, who, str(document_id), note, self.clock.now()),
        )

    def search(self, who: Actor, query: str, limit: int) -> dict[str, Any]:
        # Retrieval must see current approval/access, even for a reused request key.
        with self.database.connection() as conn:
            repository.lock_audit(conn)
            return knowledge.retrieve(conn, who, query, self.clock.now(), limit)

    def graph(self, who: Actor, limit: int, offset: int) -> dict[str, Any]:
        with self.database.connection() as conn:
            return knowledge.dependency_graph(conn, who, limit=limit, offset=offset)

    def create_template(self, who: Actor, key: UUID, data: dict[str, Any]) -> dict[str, Any]:
        return self._write(
            who,
            key,
            "template",
            "template:create",
            data,
            lambda conn: knowledge.create_template(conn, who, data, self.clock.now()),
        )

    def templates(self, who: Actor, limit: int, offset: int) -> list[dict]:
        with self.database.connection() as conn:
            return knowledge.list_templates(conn, who, limit=limit, offset=offset)

    def qa_template(self, who: Actor, key: UUID, template_id: UUID) -> dict[str, Any]:
        return self._write(
            who,
            key,
            f"template/{template_id}/qa",
            "template:qa",
            {},
            lambda conn: knowledge.qa_template(conn, who, str(template_id), self.clock.now()),
        )

    def approve_template(
        self, who: Actor, key: UUID, template_id: UUID, note: str
    ) -> dict[str, Any]:
        return self._write(
            who,
            key,
            f"template/{template_id}/approve",
            "template:approve",
            {"note": note},
            lambda conn: knowledge.approve_template(
                conn, who, str(template_id), note, self.clock.now()
            ),
        )

    def create_case(self, who: Actor, key: UUID, data: dict[str, Any]) -> dict[str, Any]:
        return self._write(
            who,
            key,
            "case",
            "case:create",
            data,
            lambda conn: runtime.create_case(conn, who, data, self.clock.now()),
        )

    def outreach(self, who: Actor, key: UUID, case_id: UUID, template_id: UUID) -> dict[str, Any]:
        return self._write(
            who,
            key,
            f"case/{case_id}/outreach",
            "attempt:create",
            {"template_id": str(template_id)},
            lambda conn: execute(
                conn,
                who,
                str(case_id),
                str(template_id),
                str(key),
                self.settings.approval_secret,
                self.clock,
                self.classifier,
            ),
        )

    def receive_conversation(
        self, who: Actor, key: UUID, case_id: UUID, data: dict[str, Any]
    ) -> dict[str, Any]:
        return self._write(
            who,
            key,
            f"case/{case_id}/conversation",
            "conversation:write",
            data,
            lambda conn: runtime.receive_conversation(
                conn, who, str(case_id), data["text"], self.clock.now()
            ),
        )

    def conversations(self, who: Actor, case_id: UUID) -> list[dict]:
        with self.database.connection() as conn:
            return runtime.read_conversations(conn, who, str(case_id))

    def events(self, who: Actor) -> list[dict]:
        require(who, "audit:read")
        with self.database.connection() as conn:
            return repository.audit_events(conn, who)

    def verify(self, who: Actor) -> dict[str, Any]:
        require(who, "audit:verify")
        with self.database.connection() as conn:
            result = verify_chain(conn)
            append_event(
                conn,
                who,
                "audit.verified",
                {"valid": result["valid"], "checked": result["checked"]},
                self.clock.now(),
            )
            return result
