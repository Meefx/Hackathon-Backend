"""The collection adapter consumes approved KMS evidence through its public boundary."""

import hashlib
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.integrations.knowledge.scope import resolve_actor
from app.integrations.knowledge.trust_adapter import InProcessKnowledgeGateway
from app.modules.knowledge import service as knowledge
from app.modules.knowledge_context.contracts import KnowledgeQuery
from app.modules.knowledge_context.service import snapshot_hash
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.shared.idempotency import digest
from tests.test_knowledge import approved_document


@pytest.fixture
def assigned(trust_conn, trust_now):
    trust_conn.execute(
        "INSERT INTO accounts(id,borrower_name,loan_reference,outstanding_amount,due_date,assigned_to) "
        "VALUES ('kms-account','Synthetic Borrower','kms-loan',100000,%s,'Synthetic Owner')",
        (trust_now.date(),),
    )
    trust_conn.execute(
        "INSERT INTO collection_authorities "
        "(account_id,principal,product,borrower_ref,borrower_timezone,borrower_owned,verified,"
        "opted_out,is_synthetic,data_verified_at,mandate_active,calendar_years,holidays) "
        "VALUES (%s,'cf','spaylater',%s,'Asia/Jakarta',TRUE,TRUE,FALSE,TRUE,%s,TRUE,%s,%s)",
        ("kms-account", uuid4(), trust_now, Jsonb([2026]), Jsonb([])),
    )
    return Caller(account_id="kms-account")


def query(now, **changes):
    return KnowledgeQuery.model_validate(
        {
            "query": "borrower collection policy",
            "purpose": "collection_draft",
            "deadline_at": now + timedelta(seconds=10),
            **changes,
        }
    )


def response(trust_conn, assigned, trust_now, **changes):
    gateway = InProcessKnowledgeGateway(clock=lambda: trust_now)
    return gateway.query_with_connection(trust_conn, query(trust_now, **changes), assigned)


def test_scope_comes_from_backend_assignment(trust_conn, assigned, trust_now):
    actor = resolve_actor(trust_conn, assigned, trust_now)
    assert (actor.principal, actor.product, actor.role) == ("cf", "spaylater", "operator")
    with pytest.raises(DomainError, match="scope"):
        resolve_actor(trust_conn, replace(assigned, principal_id="model-admin"), trust_now)


def test_missing_scope_denies(trust_conn, trust_now):
    with pytest.raises(DomainError, match="scope"):
        resolve_actor(trust_conn, Caller(), trust_now)


def test_approved_metadata_and_three_hashes_map_exactly(
    trust_conn, trust_actor, assigned, trust_now
):
    document = approved_document(trust_conn, trust_actor, trust_now, access_roles=["operator"])
    result = response(trust_conn, assigned, trust_now)
    source = result.sources[0]
    assert source.document_id == document["id"]
    assert source.approved_at == trust_now
    assert source.effective_from == trust_now - timedelta(days=1)
    assert source.document_hash == document["content_hash"]
    assert source.snippet_hash == hashlib.sha256(source.excerpt.encode()).hexdigest()
    assert source.content_hash == digest(source.excerpt)
    assert source.content_hash != source.snippet_hash


def test_versioned_citations_do_not_collide(trust_conn, trust_actor, assigned, trust_now):
    approved_document(
        trust_conn, trust_actor, trust_now, access_roles=["operator"], source_version="v1"
    )
    approved_document(
        trust_conn, trust_actor, trust_now, access_roles=["operator"], source_version="v2"
    )
    result = response(trust_conn, assigned, trust_now, top_k=5)
    assert len({s.citation_id for s in result.sources}) == 2


def test_synthetic_uri_is_nonclickable_provenance(trust_conn, trust_actor, assigned, trust_now):
    approved_document(trust_conn, trust_actor, trust_now, access_roles=["operator"])
    assert (
        response(trust_conn, assigned, trust_now).sources[0].source_uri.startswith("synthetic://")
    )


def test_critical_context_is_preserved_or_blocked(trust_conn, trust_actor, assigned, trust_now):
    approved_document(
        trust_conn,
        trust_actor,
        trust_now,
        access_roles=["operator"],
        criticality="critical",
        text="# Policy\nUse polite collection.\n# Exceptions\nDisputes pause collection.",
    )
    result = response(trust_conn, assigned, trust_now)
    assert any("Disputes pause collection" in c.text for c in result.critical_context)
    assert all(c.document_version and c.content_hash for c in result.critical_context)
    assert result.verification_profile["context_expansion"] == "full_source_within_budget"


def test_critical_context_tampering_changes_v2_hash(trust_conn, trust_actor, assigned, trust_now):
    approved_document(
        trust_conn, trust_actor, trust_now, access_roles=["operator"], criticality="critical"
    )
    result = response(trust_conn, assigned, trust_now)
    sources = [s.model_dump(mode="json") for s in result.sources]
    context = [c.model_dump(mode="json") for c in result.critical_context]
    original = snapshot_hash(sources, context, result.verification_profile, 2)
    context[0]["text"] += " changed"
    assert snapshot_hash(sources, context, result.verification_profile, 2) != original
    assert snapshot_hash(sources, [], None, 1) == digest(sources)


def test_deadline_and_naive_timestamp_fail_closed(trust_conn, assigned, trust_now):
    result = response(trust_conn, assigned, trust_now, deadline_at=trust_now)
    assert result.status == "error" and result.reason_code == "knowledge_deadline_exceeded"
    with pytest.raises(ValidationError):
        query(trust_now, deadline_at=trust_now.replace(tzinfo=None))
    gateway = InProcessKnowledgeGateway(clock=lambda: trust_now.replace(tzinfo=None))
    invalid_clock = gateway.query_with_connection(trust_conn, query(trust_now), assigned)
    assert invalid_clock.reason_code == "knowledge_clock_invalid"


def test_reference_guard_rejects_tampering_and_retirement(
    trust_conn, trust_actor, assigned, trust_now
):
    document = approved_document(trust_conn, trust_actor, trust_now, access_roles=["operator"])
    result = response(trust_conn, assigned, trust_now)
    gateway = InProcessKnowledgeGateway(clock=lambda: trust_now)
    assert gateway.lock_references(trust_conn, result.sources, assigned).validate(trust_now).valid
    tampered = result.sources[0].model_copy(update={"snippet_id": str(uuid4())})
    assert not gateway.lock_references(trust_conn, [tampered], assigned).validate(trust_now).valid
    knowledge.retire(trust_conn, trust_actor("compliance"), document["id"], "Superseded", trust_now)
    assert (
        not gateway.lock_references(trust_conn, result.sources, assigned).validate(trust_now).valid
    )


def test_template_context_retains_all_dependencies(trust_conn, trust_actor, assigned, trust_now):
    from tests.test_knowledge import approved_template

    document = approved_document(
        trust_conn,
        trust_actor,
        trust_now,
        access_roles=["operator", "knowledge"],
        criticality="critical",
    )
    template = approved_template(trust_conn, trust_actor, trust_now, document["id"])
    gateway = InProcessKnowledgeGateway(clock=lambda: trust_now)
    binding = gateway.load_template(trust_conn, template["id"], assigned, trust_now)
    context = gateway.template_context(trust_conn, binding, assigned, trust_now)
    assert context.status == "ready"
    assert {source.document_id for source in context.sources} == {document["id"]}
    assert context.critical_context
    guard = gateway.lock_references(trust_conn, context.sources, assigned)
    assert guard.validate_template(binding, trust_now)
    assert not guard.validate_template({**binding, "artifact_sha256": "tampered"}, trust_now)


def test_retrieval_reads_clock_after_governance_lock(
    monkeypatch, trust_conn, trust_actor, assigned, trust_now
):
    approved_document(
        trust_conn,
        trust_actor,
        trust_now,
        access_roles=["operator"],
        effective_until=(trust_now + timedelta(seconds=1)).isoformat(),
    )
    clock = [trust_now]
    original = knowledge.lock_governance

    def after_wait(connection):
        original(connection)
        clock[0] = trust_now + timedelta(seconds=2)

    monkeypatch.setattr(knowledge, "lock_governance", after_wait)
    gateway = InProcessKnowledgeGateway(clock=lambda: clock[0])
    result = gateway.query_with_connection(trust_conn, query(trust_now), assigned)
    assert result.status == "no_results"


@pytest.fixture
def evidence_run(trust_conn, assigned, trust_now):
    session_id, run_id = str(uuid4()), str(uuid4())
    trust_conn.execute(
        "INSERT INTO manager_sessions(id,account_id,caller_id,title,created_at,updated_at) "
        "VALUES (%s,%s,'local-operator','Synthetic evidence run',%s,%s)",
        (session_id, assigned.account_id, trust_now, trust_now),
    )
    trust_conn.execute(
        "INSERT INTO collection_runs(id,account_id,manager_session_id,caller_id,request_key,"
        "payload,status,requested_runtime_mode,session_turn_index,created_at) "
        "VALUES (%s,%s,%s,'local-operator',%s,%s,'running','simulated',1,%s)",
        (run_id, assigned.account_id, session_id, str(uuid4()), Jsonb({}), trust_now),
    )
    return run_id


def test_snapshot_persists_critical_context_and_is_immutable_after_retirement(
    trust_conn, trust_actor, assigned, trust_now, evidence_run
):
    from app.modules.knowledge_context.service import (
        list_snapshots,
        persist_snapshot,
        validate_citations,
    )

    document = approved_document(
        trust_conn, trust_actor, trust_now, access_roles=["operator"], criticality="critical"
    )
    result = response(trust_conn, assigned, trust_now)
    saved = persist_snapshot(trust_conn, evidence_run, result, trust_now)
    knowledge.retire(trust_conn, trust_actor("compliance"), document["id"], "Superseded", trust_now)
    loaded = list_snapshots(trust_conn, evidence_run, 20, 0)["items"][0]
    assert loaded["snapshot_hash"] == saved["snapshot_hash"]
    assert loaded["critical_context"] == saved["critical_context"]
    assert loaded["snapshot_hash_version"] == 2
    binding, _ = validate_citations(trust_conn, evidence_run, [result.sources[0].citation_id])
    assert binding["references"][0]["snapshot_id"] == saved["id"]
    with pytest.raises(psycopg.Error), trust_conn.transaction():
        trust_conn.execute(
            "UPDATE knowledge_snapshots SET critical_context='[]' WHERE id=%s", (saved["id"],)
        )


def test_legacy_snapshot_hash_and_earliest_citation_binding_remain_readable(
    trust_conn, trust_actor, assigned, trust_now, evidence_run
):
    from app.modules.knowledge_context.service import persist_snapshot, validate_citations

    approved_document(trust_conn, trust_actor, trust_now, access_roles=["operator"])
    result = response(trust_conn, assigned, trust_now)
    # Simulate pre-integration stored evidence, which did not have new optional raw hash keys.
    sources = [
        source.model_dump(mode="json", exclude={"document_hash", "snippet_hash"})
        for source in result.sources
    ]
    old_id = str(uuid4())
    trust_conn.execute(
        "INSERT INTO knowledge_snapshots(id,run_id,status,source_mode,required,retrieved_at,"
        "snapshot_hash,sources) VALUES (%s,%s,'ready','knowledge_module',TRUE,%s,%s,%s)",
        (old_id, evidence_run, trust_now, digest(sources), Jsonb(sources)),
    )
    # The repeated citation must bind the first snapshot even after a later retrieval.
    same = result.model_copy(
        update={
            "sources": [
                source.model_copy(update={"document_hash": None, "snippet_hash": None})
                for source in result.sources
            ]
        }
    )
    persist_snapshot(trust_conn, evidence_run, same, trust_now + timedelta(seconds=1))
    binding, _ = validate_citations(trust_conn, evidence_run, [sources[0]["citation_id"]])
    assert binding["references"][0]["snapshot_id"] == old_id
    assert binding["references"][0]["snapshot_hash"] == digest(sources)
    assert binding["references"][0]["snapshot_hash_version"] == 1


def test_critical_over_budget_returns_review_without_truncation(
    trust_conn, trust_actor, assigned, trust_now
):
    approved_document(
        trust_conn,
        trust_actor,
        trust_now,
        access_roles=["operator"],
        criticality="critical",
        text="# Policy\n" + ("Respect the borrower and pause disputes. " * 750),
    )
    result = response(trust_conn, assigned, trust_now)
    assert result.status == "error"
    assert result.reason_code == "critical_context_requires_review"
    assert result.sources == [] and result.critical_context == []


def test_query_deadline_sets_database_statement_timeout(
    monkeypatch, trust_conn, assigned, trust_now
):
    def slow_retrieval(connection, *args):
        connection.execute("SELECT pg_sleep(0.2)")

    monkeypatch.setattr(knowledge, "retrieve", slow_retrieval)
    gateway = InProcessKnowledgeGateway(clock=lambda: trust_now)
    with pytest.raises(psycopg.errors.QueryCanceled), trust_conn.transaction():
        gateway.query_with_connection(
            trust_conn,
            query(trust_now, deadline_at=trust_now + timedelta(milliseconds=20)),
            assigned,
        )


def test_different_knowledge_database_rejects_existing_evidence(
    trust_conn, trust_actor, assigned, trust_now
):
    from app.config import get_settings

    approved_document(trust_conn, trust_actor, trust_now, access_roles=["operator"])
    result = response(trust_conn, assigned, trust_now)
    settings = get_settings().model_copy(
        update={"trust_database_url": "postgresql://synthetic@127.0.0.1:5433/another_test"}
    )
    gateway = InProcessKnowledgeGateway(settings=settings, clock=lambda: trust_now)
    denied = gateway.query_with_connection(trust_conn, query(trust_now), assigned)
    assert denied.reason_code == "knowledge_configuration_invalid"
    with pytest.raises(DomainError) as failure:
        gateway.lock_references(trust_conn, result.sources, assigned)
    assert failure.value.code == "knowledge_configuration_invalid"
    # Connection-local options do not change the database endpoint.
    equivalent = settings.model_copy(
        update={
            "trust_database_url": psycopg.conninfo.make_conninfo(
                settings.database_url, options="-c statement_timeout=50"
            )
        }
    )
    same = InProcessKnowledgeGateway(settings=equivalent, clock=lambda: trust_now)
    assert same.lock_references(trust_conn, result.sources, assigned).validate(trust_now).valid


def test_database_budget_shrinks_after_scope_and_ranking(
    monkeypatch, trust_conn, trust_actor, assigned, trust_now
):
    from app.integrations.knowledge import trust_adapter
    from app.modules.knowledge import repository

    approved_document(
        trust_conn, trust_actor, trust_now, access_roles=["operator"], criticality="critical"
    )
    clock = [trust_now]
    original_scope = trust_adapter.resolve_actor
    original_search = repository.search_chunks
    original_expand = repository.expand_critical
    observed = []

    def scope(*args):
        actor = original_scope(*args)
        clock[0] = trust_now + timedelta(seconds=8)
        return actor

    def search(connection, parameters):
        observed.append(
            connection.execute(
                "SELECT current_setting('statement_timeout')::interval <= interval '2 seconds' AS bounded"
            ).fetchone()["bounded"]
        )
        rows = original_search(connection, parameters)
        clock[0] = trust_now + timedelta(milliseconds=9500)
        return rows

    def expand(connection, parameters):
        observed.append(
            connection.execute(
                "SELECT current_setting('statement_timeout')::interval <= interval '0.5 seconds' AS bounded"
            ).fetchone()["bounded"]
        )
        return original_expand(connection, parameters)

    monkeypatch.setattr(trust_adapter, "resolve_actor", scope)
    monkeypatch.setattr(repository, "search_chunks", search)
    monkeypatch.setattr(repository, "expand_critical", expand)
    gateway = InProcessKnowledgeGateway(clock=lambda: clock[0])
    assert gateway.query_with_connection(trust_conn, query(trust_now), assigned).status == "ready"
    assert observed == [True, True]


def test_expired_lock_budget_prevents_ranking(monkeypatch, trust_conn, assigned, trust_now):
    clock = [trust_now]
    original = knowledge.lock_governance

    def wait(connection):
        original(connection)
        clock[0] = trust_now + timedelta(seconds=11)

    def forbidden(*args, **kwargs):
        raise AssertionError("Expired budget must not begin ranking")

    monkeypatch.setattr(knowledge, "lock_governance", wait)
    monkeypatch.setattr(knowledge, "retrieve", forbidden)
    gateway = InProcessKnowledgeGateway(clock=lambda: clock[0])
    assert (
        gateway.query_with_connection(trust_conn, query(trust_now), assigned).reason_code
        == "knowledge_deadline_exceeded"
    )


def test_deadline_after_audit_rolls_back_retrieval(
    monkeypatch, trust_conn, trust_actor, assigned, trust_now
):
    from app.modules.knowledge import retrieval

    approved_document(trust_conn, trust_actor, trust_now, access_roles=["operator"])
    clock = [trust_now]
    original = retrieval.append_event

    def audit_then_expire(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] = trust_now + timedelta(seconds=11)
        return result

    monkeypatch.setattr(retrieval, "append_event", audit_then_expire)
    gateway = InProcessKnowledgeGateway(clock=lambda: clock[0])
    assert (
        gateway.query_with_connection(trust_conn, query(trust_now), assigned).reason_code
        == "knowledge_deadline_exceeded"
    )
    assert (
        trust_conn.execute(
            "SELECT count(*) AS n FROM trust.audit_events WHERE event_type='knowledge.retrieved'"
        ).fetchone()["n"]
        == 0
    )


def test_outer_query_deadline_prevents_commit(
    monkeypatch, trust_conn, trust_actor, assigned, trust_now
):
    approved_document(trust_conn, trust_actor, trust_now, access_roles=["operator"])
    trust_conn.commit()
    clock = [trust_now]
    gateway = InProcessKnowledgeGateway(clock=lambda: clock[0])
    original = gateway.query_with_connection

    def expire_before_commit(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result.status == "ready"
        clock[0] = trust_now + timedelta(seconds=11)
        return result

    monkeypatch.setattr(gateway, "query_with_connection", expire_before_commit)
    result = gateway.query_approved(query(trust_now), assigned)
    assert result.reason_code == "knowledge_deadline_exceeded"
    assert (
        trust_conn.execute(
            "SELECT count(*) AS n FROM trust.audit_events WHERE event_type='knowledge.retrieved'"
        ).fetchone()["n"]
        == 0
    )


def test_idle_connection_is_denied_without_committing_retrieval(
    trust_conn, trust_actor, assigned, trust_now
):
    approved_document(trust_conn, trust_actor, trust_now, access_roles=["operator"])
    trust_conn.commit()
    gateway = InProcessKnowledgeGateway(clock=lambda: trust_now)
    result = gateway.query_with_connection(trust_conn, query(trust_now), assigned)
    assert result.reason_code == "knowledge_transaction_required"
    trust_conn.rollback()
    assert (
        trust_conn.execute(
            "SELECT count(*) AS n FROM trust.audit_events WHERE event_type='knowledge.retrieved'"
        ).fetchone()["n"]
        == 0
    )
