"""Source archive and draft staging. No operation grants legal approval or dispatch."""

import re
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg import Connection
from psycopg_pool import ConnectionPool

from app.config import TrustSettings
from app.core.clock import Clock
from app.integrations.knowledge_sources.extract import SourceExtractionError, extract_provision
from app.modules.knowledge_sources import repository
from app.modules.knowledge_sources.preparation import PreparedSource, digest, validate_prepared
from app.modules.knowledge_sources.schemas import ProvisionSpec
from app.trust import knowledge
from app.trust.audit import append_event, canonical
from app.trust.errors import TrustError
from app.trust.processing import chunk_sections
from app.trust.security import Actor, require


def effective_instant(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        instant = datetime.fromisoformat(value)
        # A legal calendar date starts in Indonesia; never assume a UTC date boundary.
        if len(value) == 10:
            instant = instant.replace(tzinfo=ZoneInfo("Asia/Jakarta"))
        if instant.tzinfo is None:
            raise ValueError("timezone required")
        return instant
    except (ValueError, TypeError):
        raise TrustError("INVALID_EFFECTIVE_INTERVAL", 422) from None


def provision_text(source: PreparedSource, provision: ProvisionSpec) -> str:
    result = extract_provision(
        source.pages, provision.page_start, provision.page_end, provision.article, provision.section
    )
    text = result.text
    if provision.start_marker:
        # Match flexible PDF whitespace, but preserve original characters in the excerpt.
        pattern = r"\s+".join(re.escape(part) for part in provision.start_marker.split())
        starts = list(re.finditer(pattern, text, re.IGNORECASE))
        if len(starts) != 1:
            raise SourceExtractionError("PROVISION_MARKER_AMBIGUOUS")
        text = text[starts[0].start() :]
    if provision.end_marker:
        pattern = r"\s+".join(re.escape(part) for part in provision.end_marker.split())
        ends = list(re.finditer(pattern, text, re.IGNORECASE))
        if len(ends) != 1:
            raise SourceExtractionError("PROVISION_MARKER_AMBIGUOUS")
        text = text[: ends[0].start()]
    if not text.strip():
        raise SourceExtractionError("PROVISION_EMPTY")
    return text.strip()


class SourceService:
    def __init__(
        self,
        settings: TrustSettings | None,
        database: ConnectionPool[Connection[dict[str, Any]]],
        clock: Clock,
    ) -> None:
        self.settings, self.database, self.clock = settings, database, clock

    def stage(self, who: Actor, source: PreparedSource) -> dict[str, Any]:
        require(who, "knowledge:ingest")
        spec = source.spec
        if not any((s.principal, s.product) == (who.principal, who.product) for s in spec.scopes):
            raise TrustError("SOURCE_SCOPE_DENIED", 403)
        validate_prepared(source)
        now = self.clock.now()
        start, end = effective_instant(spec.effective_from), effective_instant(spec.effective_until)
        if start and end and end <= start:
            raise TrustError("INVALID_EFFECTIVE_INTERVAL", 422)
        manifest = spec.model_dump(mode="json")
        # Local file locations are operational details, not public evidence metadata.
        for field in ("download_path", "pages_path", "text_path"):
            manifest.pop(field, None)
        manifest.update(
            {
                "extraction_sha256": source.extraction_sha256,
                "extraction_method": source.extraction_method,
                "extraction_review_required": source.extraction_review_required,
                "retrieved_text_is_untrusted": True,
                "legal_approval": "pending_independent_source_review_qa_and_human",
            }
        )
        manifest_hash = digest(canonical(manifest).encode())
        # Complete expensive parsing/extraction before taking the audit transaction lock.
        extracts: list[tuple[ProvisionSpec, str, str | None]] = []
        for provision in spec.provisions:
            try:
                extracts.append((provision, provision_text(source, provision), None))
            except SourceExtractionError as exc:
                extracts.append((provision, "", exc.code))
        with self.database.connection() as conn:
            repository.lock_archive(conn)
            previous = repository.existing(conn, who, spec.key, spec.sha256 or "", manifest_hash)
            if previous:
                result = repository.detail(conn, who, str(previous["id"]))
                assert result is not None
                return self._stage_result(result, replayed=True)
            repository.save_artifact(conn, spec.sha256 or "", source.body, len(source.pages), now)
            source_id = repository.save_record(
                conn,
                {
                    "principal": who.principal,
                    "product": who.product,
                    "source_key": spec.key,
                    "title": spec.title,
                    "publisher": spec.publisher,
                    "source_url": spec.source_url,
                    "final_url": source.final_url,
                    "source_version": spec.source_version,
                    "source_class": spec.source_class,
                    "artifact_sha256": spec.sha256,
                    "extracted_pages": [{"number": p.number, "text": p.text} for p in source.pages],
                    "extraction_sha256": source.extraction_sha256,
                    "extraction_method": source.extraction_method,
                    "extraction_review_required": source.extraction_review_required,
                    "manifest_sha256": manifest_hash,
                    "legal_status": spec.legal_status,
                    "effective_from": start,
                    "effective_until": end,
                    "metadata": manifest,
                    "created_by": who.subject,
                    "created_at": now,
                },
            )
            for provision, text, error in extracts:
                document_id = None
                metadata = provision.model_dump(mode="json")
                try:
                    if error:
                        raise TrustError(error, 422)
                    if spec.legal_status not in {"in_force", "partially_repealed"}:
                        raise TrustError("LEGAL_STATUS_REQUIRES_REVIEW", 422)
                    if provision.legal_status not in {"in_force", "partially_repealed"}:
                        raise TrustError("LEGAL_STATUS_REQUIRES_REVIEW", 422)
                    if (start and start > now) or (end and end <= now):
                        raise TrustError("SOURCE_NOT_CURRENT", 422)
                    effective_from = effective_instant(
                        provision.effective_from or spec.effective_from
                    )
                    effective_until = effective_instant(
                        provision.effective_until or spec.effective_until
                    )
                    if effective_from is None:
                        raise TrustError("EFFECTIVE_DATE_UNKNOWN", 422)
                    if effective_from > now or (
                        effective_until is not None and effective_until <= now
                    ):
                        raise TrustError("SOURCE_NOT_CURRENT", 422)
                    if len(text) > 24000 or len(chunk_sections(text)) > 64:
                        raise TrustError("CRITICAL_CONTEXT_REQUIRES_REVIEW", 422)
                    # An official PDF and a matching hash do not prove legal completeness.
                    # A reviewer must resolve context/cross-references before re-ingesting
                    # an independently verified version through the existing QA workflow.
                    with conn.transaction():
                        staged = knowledge.ingest(
                            conn,
                            who,
                            {
                                "title": f"{spec.source_version}: {provision.title}",
                                "text": text,
                                "source_uri": spec.source_url,
                                "source_class": spec.source_class,
                                "source_version": spec.source_version,
                                "source_locator": f"{provision.locator}; PDF pp. {provision.page_start}-{provision.page_end}; sha256:{spec.sha256}",
                                "review_status": "review_required",
                                "criticality": "critical",
                                "access_roles": ["knowledge", "qa", "compliance", "admin"],
                                "effective_from": effective_from,
                                "effective_until": effective_until,
                            },
                            now,
                        )
                    document_id = staged["id"]
                except TrustError as exc:
                    error = exc.code
                repository.save_provision(
                    conn,
                    {
                        "source_id": source_id,
                        "principal": who.principal,
                        "product": who.product,
                        "provision_key": provision.key,
                        "locator": provision.locator,
                        "page_start": provision.page_start,
                        "page_end": provision.page_end,
                        "knowledge_document_id": document_id,
                        "stage_status": "draft" if document_id else "quarantined",
                        "reason_code": error,
                        "text_sha256": digest(text.encode()),
                        "metadata": metadata,
                    },
                )
            append_event(
                conn,
                who,
                "source.archived",
                {
                    "source_id": source_id,
                    "artifact_sha256": spec.sha256,
                    "manifest_sha256": manifest_hash,
                    "provision_count": len(extracts),
                },
                now,
            )
            result = repository.detail(conn, who, source_id)
            assert result is not None
            return self._stage_result(result, replayed=False)

    @staticmethod
    def _stage_result(row: dict, *, replayed: bool) -> dict[str, Any]:
        return {
            "source_id": str(row["id"]),
            "replayed": replayed,
            "draft": sum(p["stage_status"] == "draft" for p in row["provisions"]),
            "quarantined": sum(p["stage_status"] == "quarantined" for p in row["provisions"]),
        }

    def list_sources(self, who: Actor, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        require(who, "source:read")
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 9223372036854775807
        ):
            raise TrustError("INVALID_PAGINATION", 422)
        with self.database.connection() as conn:
            return repository.list_records(conn, who, limit, offset)

    def detail(self, who: Actor, source_id: UUID) -> dict[str, Any]:
        require(who, "source:read")
        with self.database.connection() as conn:
            result = repository.detail(conn, who, str(source_id))
            if result is None:
                raise TrustError("NOT_FOUND", 404)
            return result

    def download(self, who: Actor, source_id: UUID) -> tuple[bytes, str]:
        require(who, "source:download")
        with self.database.connection() as conn:
            row = repository.download(conn, who, str(source_id))
            if row is None:
                raise TrustError("NOT_FOUND", 404)
            body = bytes(row["payload"])
            if digest(body) != row["sha256"]:
                raise TrustError("SOURCE_INTEGRITY_FAILURE", 503)
            append_event(
                conn,
                who,
                "source.downloaded",
                {"source_id": str(source_id), "artifact_sha256": row["sha256"]},
                self.clock.now(),
            )
            return body, row["sha256"]
