"""Validate and persist bounded evidence without changing historical snapshots."""

import hashlib
from urllib.parse import urlparse
from uuid import uuid4

from app.modules.knowledge_context import repository
from app.modules.knowledge_context.contracts import KnowledgeResult, KnowledgeSource
from app.shared.errors import DomainError
from app.shared.idempotency import digest


def snapshot_hash(sources, critical_context=None, verification_profile=None, version=2):
    if version == 1:
        return digest(sources)
    if version != 2:
        raise DomainError("knowledge_reference_invalid", "Knowledge hash version is invalid")
    return digest(
        {
            "version": 2,
            "sources": sources,
            "critical_context": critical_context or [],
            "verification_profile": verification_profile,
        }
    )


def validate_snapshot_integrity(snapshot):
    expected = snapshot_hash(
        snapshot["sources"],
        snapshot.get("critical_context", []),
        snapshot.get("verification_profile"),
        snapshot.get("snapshot_hash_version", 1),
    )
    if snapshot["snapshot_hash"] != expected:
        raise DomainError("knowledge_reference_invalid", "Knowledge snapshot integrity failed")


def persist_snapshot(connection, run_id, result, now):
    result = KnowledgeResult.model_validate(result)
    if now.tzinfo is None or now.utcoffset() is None:
        raise DomainError("knowledge_reference_invalid", "Knowledge clock is invalid")
    sources = [source.model_dump(mode="json") for source in result.sources]
    for source in result.sources:
        if (
            source.content_hash != digest(source.excerpt)
            or (
                source.snippet_hash is not None
                and source.snippet_hash
                != hashlib.sha256(source.excerpt.encode("utf-8")).hexdigest()
            )
            or source.approved_at > now
            or (source.effective_from and source.effective_from > now)
            or (source.effective_until and source.effective_until <= now)
        ):
            raise DomainError("knowledge_reference_invalid", "Knowledge reference is invalid")
        if source.source_uri:
            uri = urlparse(source.source_uri)
            if uri.scheme not in {"https", "http", "synthetic"} or uri.username or uri.password:
                raise DomainError("knowledge_reference_invalid", "Knowledge URI is invalid")
            if uri.scheme in {"http", "https"} and not uri.hostname:
                raise DomainError("knowledge_reference_invalid", "Knowledge URI is invalid")
    context = [chunk.model_dump(mode="json") for chunk in result.critical_context]
    selected = {(source.document_id, source.document_version) for source in result.sources}
    for chunk in result.critical_context:
        if (chunk.document_id, chunk.document_version) not in selected or hashlib.sha256(
            chunk.text.encode("utf-8")
        ).hexdigest() != chunk.content_hash:
            raise DomainError(
                "knowledge_reference_invalid", "Critical knowledge context is invalid"
            )
    snapshot = {
        "id": str(uuid4()),
        "run_id": run_id,
        "status": result.status,
        "source_mode": result.source_mode,
        "required": True,
        "retrieved_at": now.isoformat(),
        "snapshot_hash": snapshot_hash(sources, context, result.verification_profile, 2),
        "sources": sources,
        "critical_context": context,
        "verification_profile": result.verification_profile,
        "snapshot_hash_version": 2,
        "reason_code": result.reason_code,
    }
    repository.save_snapshot(connection, snapshot)
    return snapshot


def validate_citations(connection, run_id, citation_ids):
    snapshots = repository.run_snapshots(connection, run_id)
    sources = {}
    bindings = {}
    for snapshot in snapshots:
        validate_snapshot_integrity(snapshot)
        for source in snapshot["sources"]:
            citation = source["citation_id"]
            if citation in sources and (
                KnowledgeSource.model_validate(sources[citation])
                != KnowledgeSource.model_validate(source)
            ):
                raise DomainError("knowledge_reference_invalid", "Citation identity is ambiguous")
            if citation not in sources:
                sources[citation] = source
                bindings[citation] = {
                    "snapshot_id": snapshot["id"],
                    "snapshot_hash": snapshot["snapshot_hash"],
                    "snapshot_hash_version": snapshot.get("snapshot_hash_version", 1),
                    "citation_id": citation,
                    "document_id": source["document_id"],
                    "document_version": source["document_version"],
                    "snippet_id": source["snippet_id"],
                    "content_hash": source["content_hash"],
                }
    if (
        not citation_ids
        or len(set(citation_ids)) != len(citation_ids)
        or any(citation not in sources for citation in citation_ids)
    ):
        raise DomainError("knowledge_reference_invalid", "Citation does not belong to run")
    references = [bindings[citation] for citation in citation_ids]
    return {"snapshot_hash": digest(references), "references": references}, [
        KnowledgeSource.model_validate(sources[citation]) for citation in citation_ids
    ]


def list_snapshots(connection, run_id, limit=20, offset=0):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise DomainError("invalid_pagination", "Knowledge pagination is invalid", 422)
    items, total = repository.snapshot_page(connection, run_id, limit, offset)
    for item in items:
        validate_snapshot_integrity(item)
    return {"items": items, "total": total, "limit": limit, "offset": offset}
