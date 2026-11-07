from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from app.modules.knowledge import repository
from app.modules.knowledge.audit import append_event
from app.modules.knowledge.authorization import Actor, require
from app.modules.knowledge.processing import (
    PROFILE,
    _hash,
    _instant,
    _uuid,
    _vector_literal,
    chunk_sections,
    validate_knowledge,
)
from app.modules.knowledge.repository import _document
from app.trust.errors import TrustError

ROLES = {
    "manager",
    "process",
    "knowledge",
    "qa",
    "personal",
    "operator",
    "officer",
    "compliance",
    "admin",
}


def _result(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "content_hash": row["content_hash"],
        "hash": row["content_hash"],
    }


def _validate_page(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise TrustError("INVALID_LIMIT", status=422)
    if type(offset) is not int or not 0 <= offset <= 9223372036854775807:
        raise TrustError("INVALID_OFFSET", status=422)


def ingest(conn, actor: Actor, data: dict, now: datetime) -> dict:
    require(actor, "knowledge:ingest")
    repository.lock_governance(conn, ())
    text = data.get("text", "")
    if not isinstance(text, str) or not 1 <= len(text) <= 200_000:
        raise TrustError("INVALID_KNOWLEDGE_SIZE", status=422)
    validate_knowledge(text)
    source_class = data.get("source_class", "synthetic")
    review_status = data.get("review_status", "synthetic")
    criticality = data.get("criticality", "standard")
    if source_class not in {"synthetic", "primary", "guidance"}:
        raise TrustError("INVALID_SOURCE_CLASS", status=422)
    if review_status not in {"synthetic", "verified", "review_required"}:
        raise TrustError("INVALID_REVIEW_STATUS", status=422)
    if (source_class == "synthetic") != (review_status == "synthetic"):
        raise TrustError("SOURCE_STATUS_MISMATCH", status=422)
    if criticality not in {"standard", "critical"}:
        raise TrustError("INVALID_CRITICALITY", status=422)
    roles = data.get("access_roles", ["knowledge", "qa", "manager", "personal", "process"])
    if not isinstance(roles, list) or not roles or any(role not in ROLES for role in roles):
        raise TrustError("INVALID_ACCESS_ROLES", status=422)
    fields = {}
    for name in ("title", "source_uri", "source_version", "source_locator"):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise TrustError("INVALID_SOURCE_METADATA", status=422)
        validate_knowledge(value)
        fields[name] = value.strip()
    uri = urlparse(fields["source_uri"])
    if (source_class == "synthetic" and uri.scheme != "synthetic") or (
        source_class != "synthetic" and (uri.scheme != "https" or not uri.hostname)
    ):
        raise TrustError("INVALID_SOURCE_URI", status=422)
    if uri.username or uri.password:
        raise TrustError("INVALID_SOURCE_URI", status=422)
    start = _instant(data.get("effective_from"))
    assert start is not None
    end = _instant(data.get("effective_until"), optional=True)
    if end is not None and end <= start:
        raise TrustError("INVALID_EFFECTIVE_INTERVAL", status=422)
    chunks = chunk_sections(text, max_chars=1800)
    if not chunks:
        raise TrustError("EMPTY_KNOWLEDGE", status=422)
    row = repository.insert_document(
        conn,
        (
            actor.principal,
            actor.product,
            fields["title"],
            fields["source_uri"],
            source_class,
            fields["source_version"],
            fields["source_locator"],
            review_status,
            criticality,
            roles,
            start,
            end,
            text,
            _hash(text),
            actor.subject,
            now,
        ),
    )
    for ordinal, chunk in enumerate(chunks):
        repository.insert_chunk(
            conn,
            (
                row["id"],
                ordinal,
                chunk["locator"],
                chunk.get("parent_locator"),
                chunk["text"],
                _hash(chunk["text"]),
                _vector_literal(chunk["text"]),
            ),
        )
    append_event(
        conn,
        actor,
        "knowledge.ingested",
        {
            "document_id": str(row["id"]),
            "content_hash": row["content_hash"],
            "chunk_count": len(chunks),
            "source_class": source_class,
        },
        now,
    )
    return {**_result(row), "chunk_count": len(chunks), "embedding_profile": PROFILE}


def qa_review(conn, actor: Actor, document_id: str, now: datetime) -> dict:
    require(actor, "knowledge:qa")
    repository.lock_governance(conn, ())
    row = _document(conn, actor, document_id, lock=True)
    if row["status"] != "draft":
        raise TrustError("KNOWLEDGE_NOT_DRAFT")
    if row["created_by"] == actor.subject:
        raise TrustError("INDEPENDENT_APPROVER_REQUIRED", status=403)
    if row["review_status"] == "review_required":
        raise TrustError("SOURCE_REVIEW_REQUIRED")
    validate_knowledge(row["text"])
    repository.record_qa(conn, (actor.subject, row["content_hash"], now, row["id"]))
    append_event(
        conn,
        actor,
        "knowledge.qa_passed",
        {
            "document_id": str(row["id"]),
            "content_hash": row["content_hash"],
        },
        now,
    )
    return {**_result(row), "qa_passed": True}


def approve(conn, actor: Actor, document_id: str, note: str, now: datetime) -> dict:
    require(actor, "knowledge:approve")
    repository.lock_governance(conn, ())
    if not isinstance(note, str) or len(note.strip()) < 3:
        raise TrustError("APPROVAL_NOTE_REQUIRED", status=422)
    row = _document(conn, actor, document_id, lock=True)
    if row["status"] != "draft":
        raise TrustError("KNOWLEDGE_NOT_DRAFT")
    if not row["qa_by"] or row["qa_hash"] != row["content_hash"]:
        raise TrustError("QA_APPROVAL_REQUIRED")
    if actor.subject in {row["qa_by"], row["created_by"]}:
        raise TrustError("INDEPENDENT_APPROVER_REQUIRED", status=403)
    if row["review_status"] == "review_required":
        raise TrustError("SOURCE_REVIEW_REQUIRED")
    row = repository.approve_document(conn, (actor.subject, now, row["id"]))
    append_event(
        conn,
        actor,
        "knowledge.approved",
        {
            "document_id": str(row["id"]),
            "content_hash": row["content_hash"],
            "note_hash": _hash(note),
        },
        now,
    )
    return _result(row)


def retire(conn, actor: Actor, document_id: str, reason: str, now: datetime) -> dict:
    require(actor, "knowledge:retire")
    repository.lock_governance(conn, ())
    if not isinstance(reason, str) or len(reason.strip()) < 3:
        raise TrustError("RETIREMENT_REASON_REQUIRED", status=422)
    row = _document(conn, actor, document_id, lock=True)
    if row["status"] == "retired":
        return {**_result(row), "suspended_template_ids": []}
    repository.retire_document(conn, (now, row["id"]))
    suspended = repository.suspend_dependencies(
        conn, (now, row["id"], actor.principal, actor.product)
    )
    ids = sorted(str(item["id"]) for item in suspended)
    append_event(
        conn,
        actor,
        "knowledge.retired",
        {
            "document_id": str(row["id"]),
            "reason_hash": _hash(reason),
            "suspended_template_ids": ids,
        },
        now,
    )
    return {**_result({**row, "status": "retired"}), "suspended_template_ids": ids}


def assert_current_evidence(conn, actor: Actor, document_ids: list[str], now: datetime) -> None:
    if not document_ids:
        raise TrustError("EVIDENCE_REQUIRED")
    for document_id in sorted(set(document_ids)):
        row = repository.lock_current_document(
            conn, (_uuid(document_id), actor.principal, actor.product)
        )
        if (
            row["status"] != "approved"
            or row["effective_from"] > now
            or (row["effective_until"] is not None and now >= row["effective_until"])
            or row["approved_hash"] != row["content_hash"]
            or row["qa_hash"] != row["content_hash"]
        ):
            raise TrustError("EVIDENCE_NOT_CURRENT")


def list_documents(conn, actor: Actor, *, limit: int = 100, offset: int = 0) -> list[dict]:
    require(actor, "knowledge:read")
    _validate_page(limit, offset)
    rows = repository.list_documents(
        conn,
        (
            actor.principal,
            actor.product,
            actor.role,
            actor.role in {"knowledge", "qa", "compliance", "admin"},
            limit,
            offset,
        ),
    )
    return [{**row, "id": str(row["id"])} for row in rows]


def dependency_graph(conn, actor: Actor, *, limit: int = 100, offset: int = 0) -> dict:
    require(actor, "knowledge:read")
    _validate_page(limit, offset)
    rows = repository.dependency_graph(
        conn, (actor.principal, actor.product, actor.role, limit, offset)
    )
    return {
        "edges": [
            {**row, "document_id": str(row["document_id"]), "template_id": str(row["template_id"])}
            for row in rows
        ],
        "weight_semantics": "Review importance only; every dependency remains mandatory.",
    }


from app.modules.knowledge.evidence import (  # noqa: F401
    citation_id,
    lock_references,
    lock_template,
)
from app.modules.knowledge.retrieval import retrieve, template_context  # noqa: F401
from app.modules.knowledge.templates import (  # noqa: F401
    approve_template,
    create_template,
    get_template,
    list_templates,
    qa_template,
)


def lock_governance(connection):
    """Acquire the producer ordering lock on the caller's transaction before reading time."""
    repository.lock_governance(connection)
