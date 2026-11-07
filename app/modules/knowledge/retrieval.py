from __future__ import annotations

from datetime import datetime

from app.modules.knowledge import repository
from app.modules.knowledge.audit import append_event
from app.modules.knowledge.authorization import Actor, require
from app.modules.knowledge.processing import (
    PROFILE,
    _hash,
    _vector_literal,
    validate_knowledge,
)
from app.trust.errors import TrustError


def retrieve(conn, actor: Actor, query: str, now: datetime, limit: int = 5) -> dict:
    require(actor, "knowledge:read")
    repository.lock_governance(conn, ())
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise TrustError("INVALID_QUERY", status=422)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
        raise TrustError("INVALID_LIMIT", status=422)
    validate_knowledge(query)
    rows = repository.search_chunks(
        conn,
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
    context, critical_ids = expand_context(conn, results)
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
        "verification_profile": verification_profile(critical_ids),
    }


def expand_context(conn, results):
    # Critical evidence gets additional work: expand the entire approved source,
    # preserving exceptions and definitions, and recompute source/chunk hashes.
    # This is deterministic evidence checking, not proof of legal interpretation.
    critical_ids = sorted(
        {row["document_id"] for row in results if row["criticality"] == "critical"}
    )
    context = []
    if critical_ids:
        expanded = repository.expand_critical(conn, (critical_ids,))
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
                    "document_version": row["source_version"],
                    "content_hash": row["content_hash"],
                    "locator": row["locator"],
                    "parent_locator": row["parent_locator"],
                    "text": row["text"],
                }
            )
    return context, critical_ids


def verification_profile(critical_ids):
    return {
        "embedding_profile": PROFILE,
        "semantic_embeddings": False,
        "source_correctness_verified_by_code": False,
        "review_depth": "critical" if critical_ids else "standard",
        "retrieved_text_is_untrusted": True,
        "critical_integrity_checks": bool(critical_ids),
        "context_expansion": "full_source_within_budget" if critical_ids else "none",
    }


def template_context(conn, actor: Actor, template: dict, now: datetime) -> dict:
    from app.modules.knowledge.evidence import lock_template

    approved = lock_template(conn, actor, template["id"], now)
    if any(approved.get(key) != template.get(key) for key in ("artifact_sha256", "document_ids")):
        raise TrustError("TEMPLATE_BINDING_INVALID")
    ids = approved["document_ids"]
    if len(ids) > 5:
        raise TrustError("TEMPLATE_EVIDENCE_REQUIRES_REVIEW")
    rows = repository.template_source_chunks(conn, actor, ids)
    if {str(row["document_id"]) for row in rows} != set(ids):
        raise TrustError("EVIDENCE_ACCESS_DENIED", 403)
    results = [
        {
            **row,
            "id": str(row["id"]),
            "chunk_id": str(row["id"]),
            "document_id": str(row["document_id"]),
        }
        for row in rows
    ]
    context, critical_ids = expand_context(conn, results)
    return {
        "results": results,
        "critical_context": context,
        "verification_profile": verification_profile(critical_ids),
    }
