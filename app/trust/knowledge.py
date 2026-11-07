"""Approved-source retrieval and explicit, scope-bound evidence dependencies.

Local hash vectors are an offline nonsemantic baseline, not a claim of semantic
embedding quality. Weights communicate review importance, never legal authority.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from psycopg.rows import dict_row

from app.trust.audit import append_event
from app.trust.errors import TrustError
from app.trust.processing import chunk_sections, validate_knowledge
from app.trust.security import Actor, require

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
DIMENSIONS = 64
PROFILE = "local_hash_v1_nonsemantic"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_vector(text: str) -> list[float]:
    """Deterministic lexical projection; collisions possible and openly labelled."""
    vector = [0.0] * DIMENSIONS
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        vector[int.from_bytes(digest[:2], "big") % DIMENSIONS] += 1 if digest[2] & 1 else -1
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        vector[0] = 1
        return vector
    return [value / norm for value in vector]


def _vector_literal(text: str) -> str:
    return "[" + ",".join(str(value) for value in hash_vector(text)) + "]"


def _instant(value: Any, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timezone required")
        return result
    except (ValueError, TypeError) as exc:
        raise TrustError("INVALID_EFFECTIVE_INTERVAL", status=422) from exc


def _uuid(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TrustError("INVALID_ID", status=422) from exc


def _rows(conn, query: str, params=()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def _one(conn, query: str, params=()) -> dict:
    rows = _rows(conn, query, params)
    if not rows:
        raise TrustError("NOT_FOUND", status=404)
    return rows[0]


def _document(conn, actor: Actor, document_id: str, *, lock: bool = False) -> dict:
    return _one(
        conn,
        "SELECT * FROM trust.knowledge_documents WHERE id=%s AND principal=%s AND product=%s"
        + (" FOR UPDATE" if lock else ""),
        (_uuid(document_id), actor.principal, actor.product),
    )


def _template(conn, actor: Actor, template_id: str, *, lock: bool = False) -> dict:
    return _one(
        conn,
        "SELECT * FROM trust.templates WHERE id=%s AND principal=%s AND product=%s"
        + (" FOR UPDATE" if lock else ""),
        (_uuid(template_id), actor.principal, actor.product),
    )


def _result(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "content_hash": row["content_hash"],
        "hash": row["content_hash"],
    }


def ingest(conn, actor: Actor, data: dict, now: datetime) -> dict:
    require(actor, "knowledge:ingest")
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
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
    row = _one(
        conn,
        """INSERT INTO trust.knowledge_documents
        (principal,product,title,source_uri,source_class,source_version,source_locator,
         review_status,criticality,access_roles,effective_from,effective_until,text,
         content_hash,created_by,created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
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
        conn.execute(
            """INSERT INTO trust.knowledge_chunks
            (document_id,ordinal,locator,parent_locator,text,content_hash,embedding)
            VALUES (%s,%s,%s,%s,%s,%s,%s::vector)""",
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
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    row = _document(conn, actor, document_id, lock=True)
    if row["status"] != "draft":
        raise TrustError("KNOWLEDGE_NOT_DRAFT")
    if row["created_by"] == actor.subject:
        raise TrustError("INDEPENDENT_APPROVER_REQUIRED", status=403)
    if row["review_status"] == "review_required":
        raise TrustError("SOURCE_REVIEW_REQUIRED")
    validate_knowledge(row["text"])
    conn.execute(
        "UPDATE trust.knowledge_documents SET qa_by=%s,qa_hash=%s,qa_at=%s WHERE id=%s",
        (actor.subject, row["content_hash"], now, row["id"]),
    )
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
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
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
    row = _one(
        conn,
        """UPDATE trust.knowledge_documents SET status='approved',approved_by=%s,
        approved_hash=content_hash,approved_at=%s WHERE id=%s RETURNING *""",
        (actor.subject, now, row["id"]),
    )
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
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    if not isinstance(reason, str) or len(reason.strip()) < 3:
        raise TrustError("RETIREMENT_REASON_REQUIRED", status=422)
    row = _document(conn, actor, document_id, lock=True)
    if row["status"] == "retired":
        return {**_result(row), "suspended_template_ids": []}
    conn.execute(
        "UPDATE trust.knowledge_documents SET status='retired',retired_at=%s WHERE id=%s",
        (now, row["id"]),
    )
    suspended = _rows(
        conn,
        """UPDATE trust.templates SET status='suspended',suspended_at=%s
        WHERE id IN (SELECT template_id FROM trust.knowledge_dependencies WHERE document_id=%s)
        AND principal=%s AND product=%s AND status<>'suspended' RETURNING id""",
        (now, row["id"], actor.principal, actor.product),
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
        row = _one(
            conn,
            "SELECT * FROM trust.knowledge_documents WHERE id=%s AND principal=%s AND product=%s FOR SHARE",
            (_uuid(document_id), actor.principal, actor.product),
        )
        if (
            row["status"] != "approved"
            or row["effective_from"] > now
            or (row["effective_until"] is not None and now >= row["effective_until"])
            or row["approved_hash"] != row["content_hash"]
            or row["qa_hash"] != row["content_hash"]
        ):
            raise TrustError("EVIDENCE_NOT_CURRENT")


def create_template(conn, actor: Actor, data: dict, now: datetime) -> dict:
    require(actor, "template:create")
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    body = data.get("body", "")
    title = data.get("title", "Approved-source template")
    if not isinstance(body, str) or not body.strip() or len(body) > 2000:
        raise TrustError("INVALID_TEMPLATE", status=422)
    if not isinstance(title, str) or not title.strip() or len(title) > 500:
        raise TrustError("INVALID_TEMPLATE", status=422)
    validate_knowledge(title)
    validate_knowledge(body)
    ids = data.get("document_ids", [])
    if not isinstance(ids, list) or not ids or len(ids) > 20:
        raise TrustError("EVIDENCE_REQUIRED", status=422)
    ids = sorted({_uuid(value) for value in ids})
    weights = data.get("evidence_weights", {})
    if not isinstance(weights, dict):
        raise TrustError("INVALID_EVIDENCE_WEIGHT", status=422)
    for document_id in ids:
        weight = weights.get(document_id, 1)
        if isinstance(weight, bool) or not isinstance(weight, (float, int)) or not 0 < weight <= 1:
            raise TrustError("INVALID_EVIDENCE_WEIGHT", status=422)
    assert_current_evidence(conn, actor, ids, now)
    row = _one(
        conn,
        """INSERT INTO trust.templates
        (principal,product,title,body,content_hash,created_by,created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (actor.principal, actor.product, title, body, _hash(body), actor.subject, now),
    )
    for document_id in ids:
        conn.execute(
            """INSERT INTO trust.knowledge_dependencies
            (document_id,template_id,principal,product,weight) VALUES (%s,%s,%s,%s,%s)""",
            (document_id, row["id"], actor.principal, actor.product, weights.get(document_id, 1)),
        )
    append_event(
        conn,
        actor,
        "template.created",
        {
            "template_id": str(row["id"]),
            "content_hash": row["content_hash"],
            "document_ids": ids,
        },
        now,
    )
    return {**_result(row), "document_ids": ids}


def _evidence_ids(conn, template_id: Any) -> list[str]:
    return [
        str(row["document_id"])
        for row in _rows(
            conn,
            "SELECT document_id FROM trust.knowledge_dependencies WHERE template_id=%s ORDER BY document_id",
            (template_id,),
        )
    ]


def qa_template(conn, actor: Actor, template_id: str, now: datetime) -> dict:
    require(actor, "template:qa")
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    evidence = _evidence_ids(conn, _uuid(template_id))
    assert_current_evidence(conn, actor, evidence, now)
    row = _template(conn, actor, template_id, lock=True)
    if row["status"] != "draft":
        raise TrustError("TEMPLATE_NOT_DRAFT")
    if row["created_by"] == actor.subject:
        raise TrustError("INDEPENDENT_APPROVER_REQUIRED", status=403)
    validate_knowledge(row["body"])
    conn.execute(
        "UPDATE trust.templates SET qa_by=%s,qa_hash=%s WHERE id=%s",
        (actor.subject, row["content_hash"], row["id"]),
    )
    append_event(
        conn,
        actor,
        "template.qa_passed",
        {
            "template_id": str(row["id"]),
            "content_hash": row["content_hash"],
        },
        now,
    )
    return {**_result(row), "qa_passed": True}


def approve_template(conn, actor: Actor, template_id: str, note: str, now: datetime) -> dict:
    require(actor, "template:approve")
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    if not isinstance(note, str) or len(note.strip()) < 3:
        raise TrustError("APPROVAL_NOTE_REQUIRED", status=422)
    evidence = _evidence_ids(conn, _uuid(template_id))
    assert_current_evidence(conn, actor, evidence, now)
    row = _template(conn, actor, template_id, lock=True)
    if row["status"] != "draft":
        raise TrustError("TEMPLATE_NOT_DRAFT")
    if not row["qa_by"] or row["qa_hash"] != row["content_hash"]:
        raise TrustError("QA_APPROVAL_REQUIRED")
    if actor.subject in {row["qa_by"], row["created_by"]}:
        raise TrustError("INDEPENDENT_APPROVER_REQUIRED", status=403)
    row = _one(
        conn,
        """UPDATE trust.templates SET status='approved',approved_by=%s,
        approved_hash=content_hash,approved_at=%s WHERE id=%s RETURNING *""",
        (actor.subject, now, row["id"]),
    )
    append_event(
        conn,
        actor,
        "template.approved",
        {
            "template_id": str(row["id"]),
            "content_hash": row["content_hash"],
            "note_hash": _hash(note),
        },
        now,
    )
    return _result(row)


def get_template(conn, actor: Actor, template_id: str, now: datetime) -> dict:
    require(actor, "knowledge:read")
    row = _template(conn, actor, template_id)
    ids = _evidence_ids(conn, row["id"])
    if row["status"] != "approved":
        raise TrustError("TEMPLATE_NOT_APPROVED")
    assert_current_evidence(conn, actor, ids, now)
    evidence = []
    for document_id in ids:
        document = _document(conn, actor, document_id)
        if actor.role not in document["access_roles"]:
            raise TrustError("EVIDENCE_ACCESS_DENIED", status=403)
        evidence.append(
            {
                "id": document_id,
                "content_hash": document["content_hash"],
                "source_version": document["source_version"],
            }
        )
    return {
        **_result(row),
        "body": row["body"],
        "document_ids": ids,
        "evidence": evidence,
        "layer2_exempt": False,
    }


def retrieve(conn, actor: Actor, query: str, now: datetime, limit: int = 5) -> dict:
    require(actor, "knowledge:read")
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise TrustError("INVALID_QUERY", status=422)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
        raise TrustError("INVALID_LIMIT", status=422)
    validate_knowledge(query)
    rows = _rows(
        conn,
        """WITH allowed AS MATERIALIZED (
        SELECT c.id,c.document_id,c.locator,c.parent_locator,c.text,c.search_vector,c.embedding,
        d.title,d.source_uri,d.source_version,d.source_locator,d.source_class,d.review_status,
        d.criticality,d.content_hash AS document_hash
        FROM trust.knowledge_chunks c JOIN trust.knowledge_documents d ON d.id=c.document_id
        WHERE d.principal=%s AND d.product=%s AND d.status='approved'
          AND d.effective_from<=%s AND (d.effective_until IS NULL OR d.effective_until>%s)
          AND %s=ANY(d.access_roles) AND d.qa_hash=d.content_hash AND d.approved_hash=d.content_hash
    ), lexical AS (
        SELECT id,row_number() OVER (ORDER BY ts_rank_cd(search_vector,
            plainto_tsquery('english',%s)) DESC,id) AS rank
        FROM allowed WHERE search_vector @@ plainto_tsquery('english',%s) LIMIT 30
    ), projected AS (
        SELECT id,row_number() OVER (ORDER BY embedding <=> %s::vector,id) AS rank
        FROM allowed ORDER BY embedding <=> %s::vector,id LIMIT 30
    ), scores AS (
        SELECT id,sum(score) AS score FROM (
          SELECT id,1.0/(60+rank) AS score FROM lexical
          UNION ALL SELECT id,1.0/(60+rank) AS score FROM projected
        ) ranked GROUP BY id
    ) SELECT a.id,a.document_id,a.locator,a.parent_locator,a.text,a.title,a.source_uri,
       a.source_version,a.source_locator,a.source_class,a.review_status,a.criticality,
       a.document_hash,s.score FROM allowed a JOIN scores s ON a.id=s.id
       ORDER BY s.score DESC,a.id LIMIT %s""",
        (
            actor.principal,
            actor.product,
            now,
            now,
            actor.role,
            query,
            query,
            _vector_literal(query),
            _vector_literal(query),
            limit,
        ),
    )
    results = []
    for row in rows:
        results.append(
            {
                **row,
                "id": str(row["id"]),
                "chunk_id": str(row["id"]),
                "document_id": str(row["document_id"]),
                "score": float(row["score"]),
            }
        )
    # Critical evidence gets additional work: expand the entire approved source,
    # preserving exceptions and definitions, and recompute source/chunk hashes.
    # This is deterministic evidence checking, not proof of legal interpretation.
    critical_ids = sorted(
        {row["document_id"] for row in results if row["criticality"] == "critical"}
    )
    context = []
    if critical_ids:
        expanded = _rows(
            conn,
            """SELECT c.id,c.document_id,c.locator,c.parent_locator,
            c.text,c.content_hash,d.text AS source_text,d.content_hash AS source_hash
            FROM trust.knowledge_chunks c JOIN trust.knowledge_documents d ON d.id=c.document_id
            WHERE c.document_id=ANY(%s::uuid[]) ORDER BY c.document_id,c.ordinal""",
            (critical_ids,),
        )
        if len(expanded) > 64 or sum(len(row["text"]) for row in expanded) > 24000:
            raise TrustError("CRITICAL_CONTEXT_REQUIRES_REVIEW")
        for row in expanded:
            if (
                _hash(row["text"]) != row["content_hash"]
                or _hash(row["source_text"]) != row["source_hash"]
            ):
                raise TrustError("EVIDENCE_INTEGRITY_FAILURE")
            context.append(
                {
                    "chunk_id": str(row["id"]),
                    "document_id": str(row["document_id"]),
                    "locator": row["locator"],
                    "parent_locator": row["parent_locator"],
                    "text": row["text"],
                }
            )
    append_event(
        conn,
        actor,
        "knowledge.retrieved",
        {
            "query_hash": _hash(query),
            "chunk_ids": [row["chunk_id"] for row in results],
            "document_ids": sorted({row["document_id"] for row in results}),
            "embedding_profile": PROFILE,
            "expanded_chunk_ids": [row["chunk_id"] for row in context],
        },
        now,
    )
    return {
        "results": results,
        "critical_context": context,
        "verification_profile": {
            "embedding_profile": PROFILE,
            "semantic_embeddings": False,
            "source_correctness_verified_by_code": False,
            "review_depth": "critical"
            if any(row["criticality"] == "critical" for row in results)
            else "standard",
            "retrieved_text_is_untrusted": True,
            "critical_integrity_checks": bool(critical_ids),
            "context_expansion": "full_source_within_budget" if critical_ids else "none",
        },
    }


def list_documents(conn, actor: Actor) -> list[dict]:
    require(actor, "knowledge:read")
    rows = _rows(
        conn,
        """SELECT id,title,status,source_class,source_version,review_status,
        criticality,effective_from,effective_until,content_hash FROM trust.knowledge_documents
        WHERE principal=%s AND product=%s AND %s=ANY(access_roles)
        AND (status='approved' OR %s) ORDER BY created_at,id""",
        (
            actor.principal,
            actor.product,
            actor.role,
            actor.role in {"knowledge", "qa", "compliance", "admin"},
        ),
    )
    return [{**row, "id": str(row["id"])} for row in rows]


def list_templates(conn, actor: Actor) -> list[dict]:
    require(actor, "knowledge:read")
    rows = _rows(
        conn,
        """SELECT id,title,status,content_hash FROM trust.templates
        WHERE principal=%s AND product=%s ORDER BY created_at,id""",
        (actor.principal, actor.product),
    )
    return [{**row, "id": str(row["id"])} for row in rows]


def dependency_graph(conn, actor: Actor) -> dict:
    require(actor, "knowledge:read")
    rows = _rows(
        conn,
        """SELECT e.document_id,e.template_id,e.relation,e.weight,
        d.status AS document_status,t.status AS template_status
        FROM trust.knowledge_dependencies e JOIN trust.knowledge_documents d ON d.id=e.document_id
        JOIN trust.templates t ON t.id=e.template_id
        WHERE e.principal=%s AND e.product=%s AND %s=ANY(d.access_roles)
        ORDER BY e.document_id,e.template_id""",
        (actor.principal, actor.product, actor.role),
    )
    return {
        "edges": [
            {**row, "document_id": str(row["document_id"]), "template_id": str(row["template_id"])}
            for row in rows
        ],
        "weight_semantics": "Review importance only; every dependency remains mandatory.",
    }
