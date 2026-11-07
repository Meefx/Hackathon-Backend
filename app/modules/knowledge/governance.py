"""Native artifact governance shared by document and template use cases."""

from datetime import datetime

from app.modules.knowledge import repository
from app.modules.knowledge.authorization import Actor
from app.modules.knowledge.processing import _uuid
from app.trust.errors import TrustError


def artifact_result(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "content_hash": row["content_hash"],
        "hash": row["content_hash"],
    }


def validate_page(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise TrustError("INVALID_LIMIT", status=422)
    if type(offset) is not int or not 0 <= offset <= 9223372036854775807:
        raise TrustError("INVALID_OFFSET", status=422)


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
