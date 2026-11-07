"""Canonical gate regression tests across KMS, contact authority and receipt atomicity."""

from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from app.config import get_settings
from app.core.clock import FrozenClock
from app.db.connection import connect
from app.modules.conversations import dispatch as gate
from app.modules.conversations.schemas import DispatchRequest
from app.shared.caller import Caller
from app.shared.errors import DomainError
from tests.integration.test_collection_safety import NOW, draft
from tests.test_trust_pipeline import APPROVAL_SECRET, mapped_case, prepared, scenario


def request():
    return DispatchRequest(expected_message_version=1, idempotency_key=uuid4())


def test_atomic_guard_unavailable_blocks(database_url, monkeypatch):
    message = draft(database_url)
    monkeypatch.setattr(gate, "get_gateway", lambda settings: object())
    result = gate.simulate_dispatch(get_settings(), message["id"], request(), Caller(), NOW)
    assert result["status"] == "blocked"
    assert "knowledge_atomic_validation_unavailable" in result["reason_codes"]
    with connect(database_url) as conn:
        assert not conn.execute("SELECT 1 FROM dispatch_receipts").fetchone()


def test_clock_after_lock_blocks_expired_or_outside_window(database_url, monkeypatch):
    message = draft(database_url)
    clock = FrozenClock(NOW)
    original = gate.lock_contact_authority

    def lock_then_advance(conn, account_id):
        result = original(conn, account_id)
        clock.instant = NOW + timedelta(hours=10, seconds=1)
        return result

    monkeypatch.setattr(gate, "lock_contact_authority", lock_then_advance)
    result = gate.simulate_dispatch(get_settings(), message["id"], request(), Caller(), clock)
    assert result["status"] == "blocked"
    assert "approval_expired" in result["reason_codes"]
    assert "outside_hours" in result["reason_codes"]
    with connect(database_url) as conn:
        assert not conn.execute("SELECT 1 FROM dispatch_receipts").fetchone()


def test_template_suspension_blocks_prior_draft(trust_conn, trust_actor, trust_now):
    _, template, case = scenario(trust_conn, trust_actor, trust_now)
    candidate = prepared(trust_conn, trust_actor, trust_now, template, case)
    trust_conn.execute(
        "UPDATE trust.templates SET status='suspended' WHERE id=%s", (template["id"],)
    )
    result = gate.dispatch(
        trust_conn, trust_actor("operator"), candidate, APPROVAL_SECRET, FrozenClock(trust_now)
    )
    assert result["status"] == "NOT_SENT"
    assert not trust_conn.execute("SELECT 1 FROM dispatch_receipts").fetchone()


def test_two_accounts_share_borrower_budget(trust_conn, trust_actor, trust_now):
    _, template, first = scenario(trust_conn, trust_actor, trust_now)
    second = mapped_case(trust_conn, trust_actor, trust_now, borrower_ref=first["borrower_ref"])
    candidate = prepared(trust_conn, trust_actor, trust_now, template, first)
    assert (
        gate.dispatch(
            trust_conn, trust_actor("operator"), candidate, APPROVAL_SECRET, FrozenClock(trust_now)
        )["status"]
        == "DELIVERED"
    )
    blocked = prepared(trust_conn, trust_actor, trust_now, template, second)
    assert blocked["status"] == "NOT_SENT"
    assert trust_conn.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 1


def test_audit_failure_rolls_back_receipt(database_url, monkeypatch):
    message = draft(database_url)

    def fail(*args, **kwargs):
        raise psycopg.OperationalError("injected account audit failure")

    monkeypatch.setattr(gate, "append_event", fail)
    with pytest.raises(psycopg.OperationalError):
        gate.simulate_dispatch(get_settings(), message["id"], request(), Caller(), NOW)
    with connect(database_url) as conn:
        assert not conn.execute("SELECT 1 FROM dispatch_receipts").fetchone()
        assert (
            conn.execute(
                "SELECT status FROM collection_messages WHERE id=%s", (message["id"],)
            ).fetchone()["status"]
            == "draft"
        )
        assert not conn.execute(
            "SELECT 1 FROM collection_requests WHERE scope=%s", ("dispatch:" + message["id"],)
        ).fetchone()


def test_dispatch_permission_checked_before_successful_replay(database_url):
    message = draft(database_url)
    key = request()
    assert gate.simulate_dispatch(get_settings(), message["id"], key, Caller(), NOW)["status"] == (
        "simulated_sent"
    )
    with pytest.raises(DomainError) as failure:
        gate.simulate_dispatch(
            get_settings(), message["id"], key, Caller(principal_id="untrusted-operator"), NOW
        )
    assert failure.value.code == "dispatch_forbidden"


def test_unmapped_legacy_case_has_no_operational_effect(trust_conn, trust_actor, trust_now):
    from app.trust.errors import TrustError
    from app.trust.runtime import create_case

    with pytest.raises(TrustError, match="legacy_case_mapping_required"):
        create_case(trust_conn, trust_actor("operator"), {"is_synthetic": True}, trust_now)
    for table in (
        "trust.cases",
        "trust.attempts",
        "trust.dispatches",
        "collection_messages",
        "dispatch_receipts",
    ):
        assert not trust_conn.execute(f"SELECT 1 FROM {table}").fetchone()


@pytest.mark.parametrize(
    "column,value,reason",
    [
        ("borrower_timezone", "Unknown", "timezone_unverified"),
        ("borrower_owned", False, "no_authorised_recipient"),
        ("verified", False, "no_authorised_recipient"),
        ("opted_out", True, "no_authorised_recipient"),
        ("mandate_active", False, "mandate_unverified"),
        ("is_synthetic", False, "simulation_required"),
        ("data_verified_at", NOW + timedelta(seconds=1), "data_invalid"),
        ("data_verified_at", NOW - timedelta(hours=24, seconds=1), "data_stale"),
        ("calendar_years", [], "calendar_missing"),
        ("holidays", ["2026-11-07"], "holiday"),
    ],
)
def test_current_authority_revalidated_before_receipt(database_url, column, value, reason):
    from psycopg import sql
    from psycopg.types.json import Jsonb

    message = draft(database_url)
    persisted = Jsonb(value) if isinstance(value, list) else value
    with connect(database_url) as conn:
        conn.execute(
            sql.SQL("UPDATE collection_authorities SET {}=%s WHERE account_id=%s").format(
                sql.Identifier(column)
            ),
            (persisted, message["account_id"]),
        )
    result = gate.simulate_dispatch(get_settings(), message["id"], request(), Caller(), NOW)
    assert result["status"] == "blocked"
    assert reason in result["reason_codes"]
    with connect(database_url) as conn:
        assert not conn.execute("SELECT 1 FROM dispatch_receipts").fetchone()


def legacy_delivery(conn, case, template, now, *, key=None):
    from psycopg.types.json import Jsonb

    attempt_id, dispatch_id = uuid4(), uuid4()
    request_key = key or uuid4()
    conn.execute(
        "INSERT INTO trust.attempts VALUES (%s,'cf','spaylater',%s,%s,%s,%s,1,'DELIVERED',"
        "%s,%s,%s,'collections-policy-v1',%s)",
        (
            attempt_id,
            case["id"],
            template["id"],
            request_key,
            "legacy-request",
            "0" * 64,
            Jsonb([]),
            Jsonb({}),
            now,
        ),
    )
    conn.execute(
        "INSERT INTO trust.dispatches VALUES (%s,%s,'cf','spaylater',%s,%s,%s,%s,"
        "'proactive',true,%s)",
        (
            dispatch_id,
            attempt_id,
            case["id"],
            case["borrower_ref"],
            "0" * 64,
            "Synthetic historical message",
            now,
        ),
    )
    return attempt_id, request_key


def test_cutover_counts_legacy_contacts_once(trust_conn, trust_actor, trust_now):
    from app.modules.compliance import repository

    _, template, case = scenario(trust_conn, trust_actor, trust_now)
    for days in (1, 2, 3):
        legacy_delivery(trust_conn, case, template, trust_now - timedelta(days=days))
    authority = repository.authority(trust_conn, case["account_id"])
    assert len(repository.contact_history(trust_conn, authority)) == 3
    candidate = prepared(trust_conn, trust_actor, trust_now, template, case)
    assert candidate["status"] == "NOT_SENT"
    assert not trust_conn.execute("SELECT 1 FROM dispatch_receipts").fetchone()


def test_legacy_delivered_attempt_never_resends(trust_conn, trust_actor, trust_now):
    _, template, case = scenario(trust_conn, trust_actor, trust_now)
    attempt, key = legacy_delivery(trust_conn, case, template, trust_now)
    replayed = prepared(trust_conn, trust_actor, trust_now, template, case, key=str(key))
    assert replayed["status"] == "DELIVERED"
    assert replayed["attempt_id"] == str(attempt)
    assert replayed["idempotent_replay"] is True
    assert not trust_conn.execute("SELECT 1 FROM dispatch_receipts").fetchone()
    assert not trust_conn.execute("SELECT 1 FROM collection_messages").fetchone()
    assert trust_conn.execute("SELECT count(*) AS n FROM trust.dispatches").fetchone()["n"] == 1


def test_cutover_preserves_holds(trust_conn, trust_actor, trust_now):
    trust_conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    _, template, case = scenario(trust_conn, trust_actor, trust_now)
    trust_conn.execute(
        "UPDATE trust.cases SET context=jsonb_set(context,'{state}','\"escalated\"') WHERE id=%s",
        (case["id"],),
    )
    candidate = prepared(trust_conn, trust_actor, trust_now, template, case)
    assert candidate["status"] == "NOT_SENT"
    assert not trust_conn.execute("SELECT 1 FROM dispatch_receipts").fetchone()


def test_inbound_opt_out_pauses_canonical_collection(database_url):
    from app.modules.conversations.schemas import ReceiveMessageRequest
    from app.modules.conversations.service import receive_message

    message = draft(database_url)
    receive_message(
        get_settings(),
        message["account_id"],
        ReceiveMessageRequest(text="Please stop contacting me.", idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    result = gate.simulate_dispatch(get_settings(), message["id"], request(), Caller(), NOW)
    assert result["status"] == "blocked"
    assert "case_paused" in result["reason_codes"]
    assert "no_authorised_recipient" in result["reason_codes"]


def test_native_template_draft_uses_normal_collection_gate(
    database_url, trust_actor, trust_now, monkeypatch
):
    from app.integrations.knowledge.trust_adapter import InProcessKnowledgeGateway
    from app.modules.conversations.schemas import DraftCandidate
    from app.modules.conversations.service import submit_draft
    from app.modules.knowledge_context.service import persist_snapshot
    from app.modules.runs.repository import finish
    from app.modules.runs.schemas import StartRunRequest
    from app.modules.runs.service import start_run
    from app.trust.db import connection
    from app.workers.collection import claim

    monkeypatch.setenv("KNOWLEDGE_MODE", "in_process")
    monkeypatch.setenv(
        "KNOWLEDGE_SERVICE_FACTORY", "app.integrations.knowledge.trust_adapter:create_gateway"
    )
    get_settings.cache_clear()
    settings = get_settings()
    with connection(database_url) as conn:
        _, template, case = scenario(conn, trust_actor, trust_now)
    accepted = start_run(
        settings,
        case["account_id"],
        StartRunRequest(instruction="Synthetic reminder", idempotency_key=uuid4()),
        Caller(),
        trust_now,
    )
    claimed = claim(settings, trust_now, "native-template-test")
    assert claimed and claimed[0]["id"] == accepted["run_id"]
    caller = Caller(account_id=case["account_id"])
    gateway = InProcessKnowledgeGateway(settings=settings, clock=lambda: trust_now)
    with connection(database_url) as conn:
        bound = gateway.load_template(conn, template["id"], caller, trust_now)
        context = gateway.template_context(conn, bound, caller, trust_now)
        snapshot = persist_snapshot(conn, accepted["run_id"], context, trust_now)
    candidate = DraftCandidate(
        run_id=accepted["run_id"],
        account_id=case["account_id"],
        execution_attempt_id=claimed[1],
        template_id=template["id"],
        text_with_placeholders=bound["body"],
        citation_ids=[source["citation_id"] for source in snapshot["sources"]],
        idempotency_key=uuid4(),
    )
    message = submit_draft(settings, candidate, caller, trust_now)
    assert message["status"] == "draft"
    with connection(database_url) as conn:
        finish(conn, accepted["run_id"], "completed", "draft_created", "Draft ready", trust_now)
        saved = conn.execute(
            "SELECT * FROM collection_message_bindings WHERE message_id=%s", (message["id"],)
        ).fetchone()
        assert saved["template_binding"]["id"] == template["id"]
        assert saved["template_binding"]["body"] == bound["body"]
        assert saved["template_binding"]["artifact_sha256"] == bound["artifact_sha256"]
        assert saved["evidence_references"][0]["snapshot_id"] == snapshot["id"]
    result = gate.simulate_dispatch(
        settings, message["id"], request(), caller, FrozenClock(trust_now)
    )
    assert result["status"] == "simulated_sent"


def test_mapping_revocation_precedes_compatibility_replay(database_url, trust_actor, trust_now):
    from fastapi.testclient import TestClient

    from app.trust.app import create_trust_app
    from app.trust.errors import TrustError
    from app.trust.settings import TrustSettings

    with connect(database_url) as conn:
        _, template, case = scenario(conn, trust_actor, trust_now)
    settings = TrustSettings(database_url, "test-jwt-material-" * 3, APPROVAL_SECRET, True)
    key = uuid4()
    with TestClient(create_trust_app(settings, FrozenClock(trust_now))) as client:
        service = client.app.state.trust_service
        assert (
            service.outreach(trust_actor("operator"), key, case["id"], template["id"])["status"]
            == "DELIVERED"
        )
        with connect(database_url) as conn:
            conn.execute("DELETE FROM legacy_case_account_mappings WHERE case_id=%s", (case["id"],))
        with pytest.raises(TrustError, match="legacy_case_mapping_required"):
            service.outreach(trust_actor("operator"), key, case["id"], template["id"])
        with connect(database_url) as conn:
            assert conn.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 1


def test_canonical_replay_denies_revoked_legacy_mapping(database_url, trust_actor, trust_now):
    from app.modules.conversations.compatibility import collection_settings

    with connect(database_url) as conn:
        document, template, case = scenario(conn, trust_actor, trust_now)
        candidate = prepared(conn, trust_actor, trust_now, template, case)
        settings = collection_settings(conn, APPROVAL_SECRET).model_copy(
            update={"database_url": database_url}
        )
    key = request()
    first = gate.simulate_dispatch(
        settings, candidate["attempt_id"], key, Caller(), FrozenClock(trust_now)
    )
    assert first["status"] == "simulated_sent"
    # A committed successful response remains idempotent after evidence retirement
    # while account mapping and caller authorization are still legitimate.
    from app.trust import knowledge

    with connect(database_url) as conn:
        knowledge.retire(
            conn, trust_actor("compliance"), document["id"], "Synthetic retirement", trust_now
        )
    assert (
        gate.simulate_dispatch(
            settings, candidate["attempt_id"], key, Caller(), FrozenClock(trust_now)
        )
        == first
    )
    with connect(database_url) as conn:
        conn.execute("DELETE FROM legacy_case_account_mappings WHERE case_id=%s", (case["id"],))
    with pytest.raises(DomainError) as failure:
        gate.simulate_dispatch(
            settings, candidate["attempt_id"], key, Caller(), FrozenClock(trust_now)
        )
    assert failure.value.code == "legacy_case_mapping_required"
    with connect(database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 1


def test_legacy_hold_requires_authority_resolution_before_local_resume(
    database_url, trust_actor, trust_now
):
    from app.modules.cases.schemas import CaseActionRequest
    from app.modules.reviews.schemas import ReviewResolution
    from app.modules.reviews.service import case_action, open_review, resolve_review

    with connect(database_url) as conn:
        _, _, case = scenario(conn, trust_actor, trust_now)
        review_id = open_review(conn, case["account_id"], "hardship", trust_now)
        conn.execute(
            "UPDATE trust.cases SET context=jsonb_set(context,'{state}',"
            "'\"escalated\"') WHERE id=%s",
            (case["id"],),
        )
    resolved = resolve_review(
        get_settings(),
        review_id,
        ReviewResolution(
            decision="keep_paused",
            expected_version=1,
            note="Keep local operator hold",
            idempotency_key=uuid4(),
        ),
        Caller(),
        trust_now,
    )
    blocked = case_action(
        get_settings(),
        case["account_id"],
        CaseActionRequest(
            expected_case_version=resolved["case"]["case_version"],
            note="Attempt local resume",
            idempotency_key=uuid4(),
        ),
        Caller(),
        trust_now,
    )
    assert not blocked["action_applied"]
    assert blocked["contact_state"] == "paused_operator"
    assert "legacy_case_paused" in blocked["reason_codes"]
    with connect(database_url) as conn:
        conn.execute(
            "UPDATE trust.cases SET context=jsonb_set(context,'{state}','\"active\"') WHERE id=%s",
            (case["id"],),
        )
    resumed = case_action(
        get_settings(),
        case["account_id"],
        CaseActionRequest(
            expected_case_version=blocked["case_version"],
            note="Resume after authority resolution",
            idempotency_key=uuid4(),
        ),
        Caller(),
        trust_now,
    )
    assert resumed["action_applied"]
    assert resumed["contact_state"] == "active"


def test_local_resume_still_checks_holiday_gate(database_url):
    from psycopg.types.json import Jsonb

    from app.modules.cases.schemas import CaseActionRequest
    from app.modules.reviews.schemas import ReviewResolution
    from app.modules.reviews.service import case_action, open_review, resolve_review
    from app.seed import seed

    seed()
    with connect(database_url) as conn:
        review_id = open_review(conn, "CF-1001", "hardship", NOW)
        conn.execute(
            "UPDATE collection_authorities SET holidays=%s WHERE account_id='CF-1001'",
            (Jsonb([NOW.date().isoformat()]),),
        )
    resolved = resolve_review(
        get_settings(),
        review_id,
        ReviewResolution(
            decision="keep_paused",
            expected_version=1,
            note="Keep operator hold",
            idempotency_key=uuid4(),
        ),
        Caller(),
        NOW,
    )
    blocked = case_action(
        get_settings(),
        "CF-1001",
        CaseActionRequest(
            expected_case_version=resolved["case"]["case_version"],
            note="Attempt holiday resume",
            idempotency_key=uuid4(),
        ),
        Caller(),
        NOW,
    )
    assert not blocked["action_applied"]
    assert blocked["contact_state"] == "paused_operator"
    assert "holiday" in blocked["reason_codes"]
