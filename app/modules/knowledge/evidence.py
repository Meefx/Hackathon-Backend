"""Public transactional evidence validation under the producer's governance locks."""

from datetime import datetime

from app.modules.knowledge import repository
from app.modules.knowledge.authorization import Actor, require
from app.modules.knowledge.processing import _hash, _uuid
from app.trust.errors import TrustError


def citation_id(document_id: str, version: str, snippet_id: str, snippet_hash: str) -> str:
    import json

    identity = json.dumps([document_id, version, snippet_id, snippet_hash], separators=(",", ":"))
    return "kms-v2-" + _hash(identity)


def current_document(row, actor: Actor, at: datetime) -> bool:
    return bool(
        row
        and row["status"] == "approved"
        and row["approved_at"] is not None
        and row["approved_at"] <= at
        and row["effective_from"] <= at
        and (row["effective_until"] is None or at < row["effective_until"])
        and row["approved_hash"] == row["document_hash"] == row["qa_hash"]
        and actor.role in row["access_roles"]
    )


class LockedEvidence:
    """Rows and locks belong exclusively to the connection supplied by the caller."""

    def __init__(self, actor: Actor, references: list, rows: dict):
        self.actor, self.references, self.rows = actor, references, rows

    def invalid_citations(self, at: datetime) -> list[str]:
        invalid = []
        for source in self.references:
            row = self.rows.get((source.document_id, source.snippet_id))
            if row is None or not current_document(row, self.actor, at):
                invalid.append(source.citation_id)
                continue
            if not (
                row["source_version"] == source.document_version
                and row["snippet_hash"] == source.snippet_hash == _hash(source.excerpt)
                and row["document_hash"] == source.document_hash
                and row["text"] == source.excerpt
                and row["approved_at"] == source.approved_at
                and row["effective_from"] == source.effective_from
                and row["effective_until"] == source.effective_until
                and row["title"] == source.title
                and row["source_uri"] == source.source_uri
                and source.citation_id
                == citation_id(
                    source.document_id,
                    source.document_version,
                    source.snippet_id,
                    source.snippet_hash,
                )
            ):
                invalid.append(source.citation_id)
        return invalid


def lock_references(conn, actor: Actor, references: list) -> LockedEvidence:
    require(actor, "knowledge:read")
    repository.lock_governance(conn)
    rows = {}
    for source in sorted(references, key=lambda value: (value.document_id, value.snippet_id)):
        try:
            document_id, chunk_id = _uuid(source.document_id), _uuid(source.snippet_id)
        except TrustError:
            continue
        rows[(source.document_id, source.snippet_id)] = repository.reference_chunk(
            conn, actor, document_id, chunk_id
        )
    return LockedEvidence(actor, references, rows)


def lock_template(conn, actor: Actor, template_id: str, at: datetime) -> dict:
    """Lock all dependencies before the template, preserving native producer order."""
    require(actor, "knowledge:read")
    repository.lock_governance(conn)
    from app.modules.knowledge.service import assert_current_evidence
    from app.modules.knowledge.templates import get_template

    ids = repository._evidence_ids(conn, _uuid(template_id))
    assert_current_evidence(conn, actor, ids, at)
    row = repository.locked_template(conn, actor, template_id)
    if row["qa_hash"] != row["content_hash"] or row["approved_hash"] != row["content_hash"]:
        raise TrustError("TEMPLATE_NOT_APPROVED")
    template = get_template(conn, actor, template_id, at)
    return {
        **template,
        "artifact_sha256": row["content_hash"],
        "approved_at": row["approved_at"].isoformat() if row["approved_at"] else None,
    }
