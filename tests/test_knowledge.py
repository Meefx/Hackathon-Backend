"""PostgreSQL integration checks for scoped evidence and revocation safety."""

import hashlib
import math
from datetime import timedelta

import psycopg
import pytest

from app.trust import knowledge
from app.trust.errors import TrustError
from app.trust.security import Actor


def document_data(now, **changes):
    data = {
        "title": "Synthetic borrower-only policy",
        "source_uri": "synthetic://trust-tests/borrower-only",
        "source_class": "synthetic",
        "source_version": "demo-v1",
        "source_locator": "Section 1",
        "effective_from": (now - timedelta(days=1)).isoformat(),
        "effective_until": None,
        "criticality": "standard",
        "text": "# Borrower-only collection\nContact only the authenticated borrower.",
        "review_status": "synthetic",
        "access_roles": ["knowledge", "qa", "compliance", "personal", "manager"],
    }
    return {**data, **changes}


def approved_document(conn, actor_factory, now, *, principal="cf", product="spaylater", **changes):
    actor = actor_factory("knowledge", principal, product)
    result = knowledge.ingest(conn, actor, document_data(now, **changes), now)
    knowledge.qa_review(conn, actor_factory("qa", principal, product), result["id"], now)
    return knowledge.approve(
        conn,
        actor_factory("compliance", principal, product),
        result["id"],
        "Reviewed synthetic demonstration evidence.",
        now,
    )


def approved_template(conn, actor_factory, now, document_id, **changes):
    result = knowledge.create_template(
        conn,
        actor_factory(),
        {
            "title": "Borrower-safe reminder",
            "body": "Please review your account in the official application.",
            "document_ids": [document_id],
            **changes,
        },
        now,
    )
    knowledge.qa_template(conn, actor_factory("qa"), result["id"], now)
    return knowledge.approve_template(
        conn, actor_factory("compliance"), result["id"], "Reviewed the exact template.", now
    )


def result_document_ids(response):
    return {row["document_id"] for row in response["results"]}


def test_local_projection_is_deterministic_and_explicitly_nonsemantic():
    first = knowledge.hash_vector("Borrower-only contact policy")
    assert first == knowledge.hash_vector("Borrower-only contact policy")
    assert len(first) == 64
    assert math.isclose(sum(value * value for value in first), 1.0)
    assert all(math.isfinite(value) for value in knowledge.hash_vector(""))


def test_draft_cannot_enter_answer_retrieval(trust_conn, trust_actor, trust_now):
    draft = knowledge.ingest(trust_conn, trust_actor(), document_data(trust_now), trust_now)
    response = knowledge.retrieve(
        trust_conn, trust_actor("personal"), "Borrower-only collection", trust_now
    )
    assert draft["status"] == "draft"
    assert draft["id"] not in result_document_ids(response)
    assert response["verification_profile"]["semantic_embeddings"] is False
    assert response["verification_profile"]["source_correctness_verified_by_code"] is False


def test_promotion_requires_qa_and_independent_human(trust_conn, trust_actor, trust_now):
    draft = knowledge.ingest(trust_conn, trust_actor(), document_data(trust_now), trust_now)
    with pytest.raises(TrustError, match="QA_APPROVAL_REQUIRED"):
        knowledge.approve(trust_conn, trust_actor("compliance"), draft["id"], "Reviewed", trust_now)
    knowledge.qa_review(trust_conn, trust_actor("qa"), draft["id"], trust_now)
    same_subject = Actor("demo-qa", "compliance", "cf", "spaylater")
    with pytest.raises(TrustError, match="INDEPENDENT_APPROVER_REQUIRED"):
        knowledge.approve(trust_conn, same_subject, draft["id"], "Reviewed", trust_now)
    result = knowledge.approve(
        trust_conn, trust_actor("compliance"), draft["id"], "Reviewed", trust_now
    )
    assert result["status"] == "approved"
    assert (
        result["content_hash"]
        == hashlib.sha256(document_data(trust_now)["text"].encode()).hexdigest()
    )


def test_author_cannot_approve_own_knowledge(trust_conn, trust_actor, trust_now):
    draft = knowledge.ingest(trust_conn, trust_actor(), document_data(trust_now), trust_now)
    knowledge.qa_review(trust_conn, trust_actor("qa"), draft["id"], trust_now)
    author_as_human = Actor("demo-knowledge", "compliance", "cf", "spaylater")
    with pytest.raises(TrustError):
        knowledge.approve(trust_conn, author_as_human, draft["id"], "Reviewed", trust_now)


def test_qa_cannot_grant_human_approval(trust_conn, trust_actor, trust_now):
    draft = knowledge.ingest(trust_conn, trust_actor(), document_data(trust_now), trust_now)
    with pytest.raises(TrustError, match="CAPABILITY_DENIED"):
        knowledge.approve(trust_conn, trust_actor("qa"), draft["id"], "Reviewed", trust_now)


def test_unverified_primary_source_cannot_be_activated(trust_conn, trust_actor, trust_now):
    draft = knowledge.ingest(
        trust_conn,
        trust_actor(),
        document_data(
            trust_now,
            source_class="primary",
            source_uri="https://example.org/synthetic-test-regulation",
            review_status="review_required",
        ),
        trust_now,
    )
    with pytest.raises(TrustError, match="SOURCE_REVIEW_REQUIRED"):
        knowledge.qa_review(trust_conn, trust_actor("qa"), draft["id"], trust_now)


def test_retrieval_is_filtered_by_entity_product_and_agent(trust_conn, trust_actor, trust_now):
    ours = approved_document(trust_conn, trust_actor, trust_now)
    other = approved_document(
        trust_conn, trust_actor, trust_now, principal="ldn", product="spinjam"
    )
    restricted = approved_document(
        trust_conn, trust_actor, trust_now, access_roles=["knowledge", "qa", "compliance"]
    )
    response = knowledge.retrieve(
        trust_conn, trust_actor("personal"), "Borrower-only collection", trust_now, limit=10
    )
    ids = result_document_ids(response)
    assert ours["id"] in ids
    assert other["id"] not in ids
    assert restricted["id"] not in ids
    with pytest.raises(TrustError, match="NOT_FOUND"):
        knowledge.assert_current_evidence(trust_conn, trust_actor(), [other["id"]], trust_now)


def test_future_and_expired_evidence_never_enters_retrieval(trust_conn, trust_actor, trust_now):
    future = approved_document(
        trust_conn,
        trust_actor,
        trust_now,
        effective_from=(trust_now + timedelta(days=1)).isoformat(),
    )
    expired = approved_document(
        trust_conn, trust_actor, trust_now, effective_until=trust_now.isoformat()
    )
    response = knowledge.retrieve(
        trust_conn, trust_actor("personal"), "Borrower-only collection", trust_now, limit=10
    )
    ids = result_document_ids(response)
    assert future["id"] not in ids
    assert expired["id"] not in ids
    for item in (future, expired):
        with pytest.raises(TrustError, match="EVIDENCE_NOT_CURRENT"):
            knowledge.assert_current_evidence(trust_conn, trust_actor(), [item["id"]], trust_now)


def test_critical_evidence_retains_citations_and_explicit_review_profile(
    trust_conn, trust_actor, trust_now
):
    document = approved_document(trust_conn, trust_actor, trust_now, criticality="critical")
    response = knowledge.retrieve(
        trust_conn, trust_actor("personal"), "Borrower-only collection", trust_now, limit=10
    )
    match = next(row for row in response["results"] if row["document_id"] == document["id"])
    assert match["source_version"] == "demo-v1"
    assert match["source_uri"].startswith("synthetic://")
    assert match["locator"]
    assert match["criticality"] == "critical"
    assert response["verification_profile"]["review_depth"] == "critical"
    assert response["verification_profile"]["retrieved_text_is_untrusted"] is True


def test_revocation_suspends_templates_even_with_low_dependency_weight(
    trust_conn, trust_actor, trust_now
):
    document = approved_document(trust_conn, trust_actor, trust_now)
    template = approved_template(
        trust_conn,
        trust_actor,
        trust_now,
        document["id"],
        evidence_weights={document["id"]: 0.01},
        layer2_exempt=True,
    )
    current = knowledge.get_template(trust_conn, trust_actor("personal"), template["id"], trust_now)
    assert current["layer2_exempt"] is False
    assert current["evidence"] == [
        {
            "id": document["id"],
            "content_hash": document["content_hash"],
            "source_version": "demo-v1",
        }
    ]
    retired = knowledge.retire(
        trust_conn, trust_actor("compliance"), document["id"], "Source replaced", trust_now
    )
    assert retired["suspended_template_ids"] == [template["id"]]
    with pytest.raises(TrustError, match="TEMPLATE_NOT_APPROVED"):
        knowledge.get_template(trust_conn, trust_actor("personal"), template["id"], trust_now)
    response = knowledge.retrieve(
        trust_conn, trust_actor("personal"), "Borrower-only collection", trust_now
    )
    assert document["id"] not in result_document_ids(response)


def test_template_dependencies_cannot_cross_legal_entity(trust_conn, trust_actor, trust_now):
    other = approved_document(
        trust_conn, trust_actor, trust_now, principal="ldn", product="spinjam"
    )
    with pytest.raises(TrustError, match="NOT_FOUND"):
        knowledge.create_template(
            trust_conn,
            trust_actor(),
            {"body": "Please review the official application.", "document_ids": [other["id"]]},
            trust_now,
        )


@pytest.mark.parametrize("weight", [0, -1, 1.1, float("nan"), float("inf"), True, "high"])
def test_dependency_weights_are_bounded_not_authorization(
    trust_conn, trust_actor, trust_now, weight
):
    document = approved_document(trust_conn, trust_actor, trust_now)
    with pytest.raises(TrustError, match="INVALID_EVIDENCE_WEIGHT"):
        knowledge.create_template(
            trust_conn,
            trust_actor(),
            {
                "body": "Please review your account.",
                "document_ids": [document["id"]],
                "evidence_weights": {document["id"]: weight},
            },
            trust_now,
        )


def test_approved_document_and_chunks_cannot_be_rewritten(trust_conn, trust_actor, trust_now):
    document = approved_document(trust_conn, trust_actor, trust_now)
    with pytest.raises(psycopg.Error), trust_conn.transaction():
        trust_conn.execute(
            "UPDATE trust.knowledge_documents SET text=%s WHERE id=%s",
            ("Changed evidence", document["id"]),
        )
    with pytest.raises(psycopg.Error), trust_conn.transaction():
        trust_conn.execute(
            "UPDATE trust.knowledge_chunks SET text=%s WHERE document_id=%s",
            ("Changed evidence", document["id"]),
        )


def test_reviewed_template_dependencies_are_immutable(trust_conn, trust_actor, trust_now):
    document = approved_document(trust_conn, trust_actor, trust_now)
    template = approved_template(trust_conn, trust_actor, trust_now, document["id"])
    with pytest.raises(psycopg.Error), trust_conn.transaction():
        trust_conn.execute(
            "DELETE FROM trust.knowledge_dependencies WHERE template_id=%s", (template["id"],)
        )


def test_retrieval_audit_contains_hashes_and_chunk_ids_not_query(
    trust_conn, trust_actor, trust_now
):
    approved_document(trust_conn, trust_actor, trust_now)
    query = "Borrower-only collection"
    response = knowledge.retrieve(trust_conn, trust_actor("personal"), query, trust_now)
    event = trust_conn.execute(
        "SELECT payload FROM trust.audit_events WHERE event_type='knowledge.retrieved' "
        "ORDER BY seq DESC LIMIT 1"
    ).fetchone()["payload"]
    assert event["query_hash"] == hashlib.sha256(query.encode()).hexdigest()
    assert set(event["chunk_ids"]) == {row["chunk_id"] for row in response["results"]}
    assert "query" not in event
    assert "text" not in event
    assert "body" not in event


def test_critical_retrieval_expands_exceptions_and_audits_them(trust_conn, trust_actor, trust_now):
    document = approved_document(
        trust_conn,
        trust_actor,
        trust_now,
        criticality="critical",
        text="# Policy\n## Reminder\nUse a polite reminder.\n## Exceptions\nDisputes pause collection.",
    )
    result = knowledge.retrieve(trust_conn, trust_actor("personal"), "polite reminder", trust_now)
    contexts = [row for row in result["critical_context"] if row["document_id"] == document["id"]]
    assert any("Disputes pause collection" in row["text"] for row in contexts)
    assert result["verification_profile"]["critical_integrity_checks"] is True
    event = trust_conn.execute(
        "SELECT payload FROM trust.audit_events WHERE event_type='knowledge.retrieved' ORDER BY seq DESC LIMIT 1"
    ).fetchone()["payload"]
    assert {row["chunk_id"] for row in contexts}.issubset(set(event["expanded_chunk_ids"]))
