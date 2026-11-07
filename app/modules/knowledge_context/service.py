from uuid import uuid4

from psycopg.types.json import Jsonb

from app.modules.knowledge_context.contracts import KnowledgeResult, KnowledgeSource
from app.shared.errors import DomainError
from app.shared.idempotency import digest


def persist_snapshot(connection, run_id, result, now):
    result = KnowledgeResult.model_validate(result)
    sources = [s.model_dump(mode="json") for s in result.sources]
    for source in result.sources:
        if (
            source.content_hash != digest(source.excerpt)
            or source.approved_at > now
            or (source.effective_from and source.effective_from > now)
            or (source.effective_until and source.effective_until <= now)
        ):
            raise DomainError("knowledge_reference_invalid", "Knowledge reference is invalid")
        if source.source_uri and not source.source_uri.startswith(("https://", "http://")):
            raise DomainError("knowledge_reference_invalid", "Knowledge URI is invalid")
    snapshot = {
        "id": str(uuid4()),
        "run_id": run_id,
        "status": result.status,
        "source_mode": result.source_mode,
        "required": True,
        "retrieved_at": now.isoformat(),
        "snapshot_hash": digest(sources),
        "sources": sources,
        "reason_code": result.reason_code,
    }
    connection.execute(
        "INSERT INTO knowledge_snapshots(id,run_id,status,source_mode,required,retrieved_at,"
        "snapshot_hash,sources,reason_code) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            snapshot["id"],
            run_id,
            result.status,
            result.source_mode,
            True,
            now,
            snapshot["snapshot_hash"],
            Jsonb(sources),
            result.reason_code,
        ),
    )
    return snapshot


def validate_citations(connection, run_id, citation_ids):
    snapshots = connection.execute(
        "SELECT * FROM knowledge_snapshots WHERE run_id=%s ORDER BY created_sequence", (run_id,)
    ).fetchall()
    # Citation identity is immutable. Pin its earliest persisted evidence, rather than a later
    # retrieval that can contain unrelated context or repeat the same citation.
    sources = {}
    bindings = {}
    for snapshot in snapshots:
        for source in snapshot["sources"]:
            citation = source["citation_id"]
            if citation not in sources:
                sources[citation] = source
                bindings[citation] = {
                    "snapshot_id": snapshot["id"],
                    "snapshot_hash": snapshot["snapshot_hash"],
                    "citation_id": citation,
                    "document_id": source["document_id"],
                    "document_version": source["document_version"],
                    "snippet_id": source["snippet_id"],
                    "content_hash": source["content_hash"],
                }
    if not citation_ids or any(c not in sources for c in citation_ids):
        raise DomainError("knowledge_reference_invalid", "Citation does not belong to run")
    references = [bindings[c] for c in citation_ids]
    return {"snapshot_hash": digest(references), "references": references}, [
        KnowledgeSource.model_validate(sources[c]) for c in citation_ids
    ]
