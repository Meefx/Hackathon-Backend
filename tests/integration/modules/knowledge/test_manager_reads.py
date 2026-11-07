"""Manager oversight reads retain scope, lifecycle and integrity boundaries."""

from datetime import timedelta

import pytest

from app.modules.knowledge import repository
from app.modules.knowledge import service as knowledge
from app.modules.knowledge.authorization import require
from app.modules.knowledge.evidence import current_document
from app.trust.errors import TrustError
from tests.integration.modules.knowledge.test_producer import (
    approved_document,
    document_data,
    result_document_ids,
)

REVIEWER_ROLES = ["knowledge", "qa", "compliance"]


def test_manager_reads_all_current_knowledge_in_own_scope_only(trust_conn, trust_actor, trust_now):
    own = approved_document(trust_conn, trust_actor, trust_now, access_roles=REVIEWER_ROLES)
    other = approved_document(
        trust_conn,
        trust_actor,
        trust_now,
        principal="ldn",
        product="spinjam",
        access_roles=REVIEWER_ROLES,
    )
    manager = trust_actor("manager")
    result = knowledge.retrieve(trust_conn, manager, "Borrower-only collection", trust_now, 10)
    assert result_document_ids(result) == {own["id"]}
    personal = knowledge.retrieve(
        trust_conn, trust_actor("personal"), "Borrower-only collection", trust_now, 10
    )
    assert not personal["results"]
    source = result["results"][0]
    row = repository.reference_chunk(trust_conn, manager, source["document_id"], source["chunk_id"])
    assert current_document(row, manager, trust_now)
    assert not current_document(row, trust_actor("personal"), trust_now)
    other_chunk = trust_conn.execute(
        "SELECT id FROM trust.knowledge_chunks WHERE document_id=%s LIMIT 1",
        (other["id"],),
    ).fetchone()["id"]
    assert repository.reference_chunk(trust_conn, manager, other["id"], other_chunk) is None


@pytest.mark.parametrize("state", ["draft", "retired", "future", "expired"])
def test_manager_cannot_retrieve_noncurrent_knowledge(trust_conn, trust_actor, trust_now, state):
    changes = {"access_roles": REVIEWER_ROLES}
    if state == "future":
        changes["effective_from"] = (trust_now + timedelta(days=1)).isoformat()
    elif state == "expired":
        changes["effective_until"] = trust_now.isoformat()
    if state == "draft":
        document = knowledge.ingest(
            trust_conn, trust_actor(), document_data(trust_now, **changes), trust_now
        )
    else:
        document = approved_document(trust_conn, trust_actor, trust_now, **changes)
    if state == "retired":
        knowledge.retire(
            trust_conn, trust_actor("compliance"), document["id"], "Source retired", trust_now
        )
    manager = trust_actor("manager")
    assert not knowledge.retrieve(trust_conn, manager, "Borrower-only collection", trust_now)[
        "results"
    ]
    chunk_id = trust_conn.execute(
        "SELECT id FROM trust.knowledge_chunks WHERE document_id=%s LIMIT 1",
        (document["id"],),
    ).fetchone()["id"]
    row = repository.reference_chunk(trust_conn, manager, document["id"], chunk_id)
    assert not current_document(row, manager, trust_now)


def test_manager_read_exception_does_not_bypass_evidence_integrity(
    trust_conn, trust_actor, trust_now
):
    document = approved_document(trust_conn, trust_actor, trust_now, access_roles=REVIEWER_ROLES)
    manager = trust_actor("manager")
    result = knowledge.retrieve(trust_conn, manager, "Borrower-only collection", trust_now)
    row = repository.reference_chunk(
        trust_conn, manager, document["id"], result["results"][0]["chunk_id"]
    )
    assert current_document(row, manager, trust_now)
    for field in ("qa_hash", "approved_hash", "document_hash"):
        assert not current_document({**row, field: "invalid-evidence-hash"}, manager, trust_now)
    assert not current_document(
        {**row, "approved_at": trust_now + timedelta(seconds=1)}, manager, trust_now
    )


def test_manager_has_no_knowledge_mutation_or_source_download_permission(
    trust_conn, trust_actor, trust_now
):
    draft = knowledge.ingest(
        trust_conn,
        trust_actor(),
        document_data(trust_now, access_roles=REVIEWER_ROLES),
        trust_now,
    )
    manager = trust_actor("manager")
    operations = (
        lambda: knowledge.ingest(trust_conn, manager, document_data(trust_now), trust_now),
        lambda: knowledge.qa_review(trust_conn, manager, draft["id"], trust_now),
        lambda: knowledge.approve(trust_conn, manager, draft["id"], "Reviewed", trust_now),
        lambda: knowledge.retire(trust_conn, manager, draft["id"], "Retire", trust_now),
        lambda: knowledge.create_template(trust_conn, manager, {}, trust_now),
    )
    for operation in operations:
        with pytest.raises(TrustError, match="CAPABILITY_DENIED"):
            operation()
    for capability in ("source:read", "source:download", "template:qa", "template:approve"):
        with pytest.raises(TrustError, match="CAPABILITY_DENIED"):
            require(manager, capability)
    assert repository._document(trust_conn, trust_actor(), draft["id"])["status"] == "draft"
