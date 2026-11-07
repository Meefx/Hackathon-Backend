"""Knowledge list boundaries stay scoped when reading a corpus in pages."""

import pytest

from app.trust import knowledge
from app.trust.errors import TrustError
from tests.test_knowledge import approved_document, approved_template, document_data


@pytest.mark.parametrize(
    "operation", [knowledge.list_documents, knowledge.list_templates, knowledge.dependency_graph]
)
@pytest.mark.parametrize(
    ("pagination", "code"),
    [
        ({"limit": 0}, "INVALID_LIMIT"),
        ({"limit": 101}, "INVALID_LIMIT"),
        ({"limit": True}, "INVALID_LIMIT"),
        ({"offset": -1}, "INVALID_OFFSET"),
        ({"offset": True}, "INVALID_OFFSET"),
        ({"offset": 2**63}, "INVALID_OFFSET"),
    ],
)
def test_invalid_pagination_is_rejected_before_database(operation, pagination, code, trust_actor):
    with pytest.raises(TrustError, match=code) as failure:
        operation(None, trust_actor(), **pagination)
    assert failure.value.status == 422


def test_document_pagination_filters_scope_and_access_before_limit(
    trust_conn, trust_actor, trust_now
):
    visible = {approved_document(trust_conn, trust_actor, trust_now)["id"] for _ in range(3)}
    approved_document(trust_conn, trust_actor, trust_now, principal="ldn", product="spinjam")
    approved_document(trust_conn, trust_actor, trust_now, access_roles=["knowledge"])
    knowledge.ingest(trust_conn, trust_actor(), document_data(trust_now), trust_now)

    first = knowledge.list_documents(trust_conn, trust_actor("personal"), limit=2)
    last = knowledge.list_documents(trust_conn, trust_actor("personal"), limit=2, offset=2)
    assert len(first) == 2
    assert len(last) == 1
    assert {row["id"] for row in first + last} == visible
    assert knowledge.list_documents(trust_conn, trust_actor("personal"), offset=3) == []


def test_template_and_graph_pages_do_not_repeat_or_cross_scope(trust_conn, trust_actor, trust_now):
    source = approved_document(trust_conn, trust_actor, trust_now)
    visible = {
        approved_template(trust_conn, trust_actor, trust_now, source["id"])["id"] for _ in range(3)
    }

    def other_actor(role="knowledge"):
        return trust_actor(role, "ldn", "spinjam")

    other_source = approved_document(
        trust_conn, trust_actor, trust_now, principal="ldn", product="spinjam"
    )
    approved_template(trust_conn, other_actor, trust_now, other_source["id"])

    pages = [
        knowledge.list_templates(trust_conn, trust_actor(), limit=2, offset=offset)
        for offset in (0, 2)
    ]
    assert [len(page) for page in pages] == [2, 1]
    assert {row["id"] for page in pages for row in page} == visible
    graph_pages = [
        knowledge.dependency_graph(trust_conn, trust_actor(), limit=2, offset=offset)["edges"]
        for offset in (0, 2)
    ]
    assert [len(page) for page in graph_pages] == [2, 1]
    assert {row["template_id"] for page in graph_pages for row in page} == visible
    assert knowledge.dependency_graph(trust_conn, trust_actor(), offset=3)["edges"] == []
