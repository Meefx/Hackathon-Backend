"""In-process producer adapter. Connection lifetime is owned by each operation's caller."""

from datetime import datetime

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from pydantic import ValidationError

from app.config import get_settings
from app.db.connection import connect
from app.integrations.knowledge.deadline import BudgetedConnection, QueryBudget
from app.integrations.knowledge.scope import resolve_actor
from app.modules.knowledge import service as producer
from app.modules.knowledge_context.contracts import (
    KnowledgeQuery,
    KnowledgeResult,
    KnowledgeSource,
    ReferenceValidation,
)
from app.shared.clock import utcnow
from app.shared.errors import DomainError
from app.shared.idempotency import digest
from app.trust.errors import TrustError


def map_source(row: dict) -> KnowledgeSource:
    return KnowledgeSource(
        document_id=row["document_id"],
        document_version=row["source_version"],
        snippet_id=row["chunk_id"],
        citation_id=producer.citation_id(
            row["document_id"], row["source_version"], row["chunk_id"], row["snippet_hash"]
        ),
        title=row["title"],
        excerpt=row["text"],
        content_hash=digest(row["text"]),
        document_hash=row["document_hash"],
        snippet_hash=row["snippet_hash"],
        approved_at=row["approved_at"],
        effective_from=row["effective_from"],
        effective_until=row["effective_until"],
        source_uri=row["source_uri"],
    )


class LockedReferences:
    def __init__(self, connection, actor, references, native):
        self.connection, self.actor, self.references, self.native = (
            connection,
            actor,
            references,
            native,
        )

    def validate(self, at: datetime) -> ReferenceValidation:
        if at.tzinfo is None or at.utcoffset() is None:
            raise DomainError("knowledge_clock_invalid", "Knowledge clock must include timezone")
        invalid = self.native.invalid_citations(at)
        invalid.extend(
            source.citation_id
            for source in self.references
            if source.content_hash != digest(source.excerpt)
        )
        return ReferenceValidation(
            valid=bool(self.references) and not invalid,
            checked_at=at,
            invalid_citation_ids=sorted(set(invalid)),
            reference_version="kms-raw-v2",
        )

    def validate_template(self, binding, at: datetime) -> bool:
        try:
            current = producer.lock_template(self.connection, self.actor, binding["id"], at)
            return all(
                current.get(key) == binding.get(key)
                for key in ("id", "artifact_sha256", "document_ids", "evidence", "body")
            )
        except (TrustError, KeyError, TypeError):
            return False


class InProcessKnowledgeGateway:
    def __init__(self, settings=None, clock=utcnow):
        self.settings = settings
        self.clock = clock

    def _settings(self):
        return self.settings or get_settings()

    def _now(self):
        instant = self.clock()
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise DomainError(
                "knowledge_clock_invalid", "Knowledge clock must include timezone", 503
            )
        return instant

    def _check_configuration(self):
        settings = self._settings()
        configured = getattr(settings, "trust_database_url", "")
        if not configured:
            return

        def endpoint(value):
            parameters = conninfo_to_dict(value)
            return (
                parameters.get("host", "").lower(),
                parameters.get("port", "5432"),
                parameters.get("dbname"),
            )

        try:
            same = endpoint(settings.database_url) == endpoint(configured)
        except (psycopg.Error, ValueError):
            same = False
        if not same:
            raise DomainError(
                "knowledge_configuration_invalid", "Knowledge requires the collection database", 503
            )

    def _query_budget(self, request):
        return QueryBudget(
            request.deadline_at, self._now, self._settings().database_statement_timeout_ms
        )

    def query_approved(self, request, caller):
        try:
            self._check_configuration()
            request = KnowledgeQuery.model_validate(request)
            budget = self._query_budget(request)
            remaining = budget.remaining_ms()
            # Installed psycopg/libpq enforces a two-second minimum connection timeout.
            timeout = max(2, min(5, remaining // 1000))
            with psycopg.connect(
                self._settings().database_url, row_factory=dict_row, connect_timeout=timeout
            ) as connection:
                budget.remaining_ms()
                with connection.transaction():
                    result = self.query_with_connection(connection, request, caller)
                    budget.prepare(connection)
                    return result
        except (psycopg.Error, TrustError, DomainError, ValidationError) as exc:
            return self._failure(exc)

    @staticmethod
    def _failure(exc):
        if isinstance(exc, psycopg.errors.QueryCanceled | psycopg.errors.LockNotAvailable):
            reason = "knowledge_deadline_exceeded"
        elif isinstance(exc, TrustError):
            reason = exc.code.lower()
        elif isinstance(exc, DomainError):
            reason = exc.code
        else:
            reason = "knowledge_unavailable"
        return KnowledgeResult(status="error", source_mode="knowledge_module", reason_code=reason)

    def query_with_connection(self, connection, request, caller):
        """Use a savepoint inside an active, non-autocommit caller transaction."""
        try:
            if (
                connection.autocommit
                or connection.info.transaction_status != TransactionStatus.INTRANS
            ):
                raise DomainError(
                    "knowledge_transaction_required",
                    "Knowledge requires an active caller transaction",
                    503,
                )
            self._check_configuration()
            request = KnowledgeQuery.model_validate(request)
            budget = self._query_budget(request)
            budget.remaining_ms()
            # A timeout after audit insertion must roll back that retrieval's writes as well.
            with connection.transaction():
                bounded = BudgetedConnection(connection, budget)
                actor = resolve_actor(bounded, caller, self._now())
                producer.lock_governance(bounded)
                budget.remaining_ms()
                native = producer.retrieve(
                    bounded, actor, request.query, self._now(), request.top_k
                )
                budget.remaining_ms()
                result = KnowledgeResult(
                    status="ready" if native["results"] else "no_results",
                    source_mode="knowledge_module",
                    sources=[map_source(row) for row in native["results"]],
                    critical_context=native["critical_context"],
                    verification_profile=native["verification_profile"],
                )
                budget.prepare(connection)
                return result
        except (TrustError, DomainError, ValidationError) as exc:
            return self._failure(exc)

    def lock_references(self, connection, references, caller):
        self._check_configuration()
        actor = resolve_actor(connection, caller, self._now())
        native = producer.lock_references(connection, actor, references)
        return LockedReferences(connection, actor, references, native)

    def validate_references(self, references, at, caller):
        try:
            with connect(self._settings().database_url) as connection:
                return self.lock_references(connection, references, caller).validate(at)
        except (psycopg.Error, TrustError, DomainError):
            return ReferenceValidation(
                valid=False,
                checked_at=at,
                invalid_citation_ids=[s.citation_id for s in references],
                reference_version="kms-unavailable",
            )

    def load_template(self, connection, template_id, caller, now):
        self._check_configuration()
        actor = resolve_actor(connection, caller, now)
        return producer.lock_template(connection, actor, template_id, now)

    def template_context(self, connection, template, caller, now):
        self._check_configuration()
        actor = resolve_actor(connection, caller, now)
        try:
            native = producer.template_context(connection, actor, template, now)
            return KnowledgeResult(
                status="ready",
                source_mode="knowledge_module",
                sources=[map_source(row) for row in native["results"]],
                critical_context=native["critical_context"],
                verification_profile=native["verification_profile"],
            )
        except (TrustError, DomainError, ValidationError) as exc:
            return self._failure(exc)

    def template_sources(self, connection, template, caller):
        context = self.template_context(connection, template, caller, self._now())
        if context.status != "ready":
            raise DomainError(
                context.reason_code or "knowledge_unavailable", "Template evidence requires review"
            )
        return context.sources


def create_gateway():
    """Factory intentionally performs no I/O and requires no initialized application pool."""
    return InProcessKnowledgeGateway()
