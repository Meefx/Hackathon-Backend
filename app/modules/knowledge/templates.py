from __future__ import annotations

from datetime import datetime

from app.modules.knowledge import repository
from app.modules.knowledge.audit import append_event
from app.modules.knowledge.authorization import Actor, require
from app.modules.knowledge.governance import artifact_result as _result
from app.modules.knowledge.governance import assert_current_evidence
from app.modules.knowledge.governance import validate_page as _validate_page
from app.modules.knowledge.processing import (
    _hash,
    _uuid,
    validate_knowledge,
)
from app.modules.knowledge.repository import _document, _evidence_ids, _template
from app.trust.errors import TrustError


def create_template(conn, actor: Actor, data: dict, now: datetime) -> dict:
    require(actor, "template:create")
    repository.lock_governance(conn, ())
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
    row = repository.insert_template(
        conn, (actor.principal, actor.product, title, body, _hash(body), actor.subject, now)
    )
    for document_id in ids:
        repository.insert_dependency(
            conn,
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


def qa_template(conn, actor: Actor, template_id: str, now: datetime) -> dict:
    require(actor, "template:qa")
    repository.lock_governance(conn, ())
    evidence = _evidence_ids(conn, _uuid(template_id))
    assert_current_evidence(conn, actor, evidence, now)
    row = _template(conn, actor, template_id, lock=True)
    if row["status"] != "draft":
        raise TrustError("TEMPLATE_NOT_DRAFT")
    if row["created_by"] == actor.subject:
        raise TrustError("INDEPENDENT_APPROVER_REQUIRED", status=403)
    validate_knowledge(row["body"])
    repository.record_template_qa(conn, (actor.subject, row["content_hash"], row["id"]))
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
    repository.lock_governance(conn, ())
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
    row = repository.approve_template(conn, (actor.subject, now, row["id"]))
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


def list_templates(conn, actor: Actor, *, limit: int = 100, offset: int = 0) -> list[dict]:
    require(actor, "knowledge:read")
    _validate_page(limit, offset)
    rows = repository.list_templates(conn, (actor.principal, actor.product, limit, offset))
    return [{**row, "id": str(row["id"])} for row in rows]
