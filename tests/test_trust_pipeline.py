"""Security invariants exercised against real PostgreSQL transactions."""

import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.core.clock import FrozenClock
from app.services.dispatch_gate import dispatch
from app.trust import knowledge
from app.trust.app import create_trust_app
from app.trust.audit import verify_chain
from app.trust.db import connection
from app.trust.pipeline import prepare
from app.trust.runtime import receive_conversation
from app.trust.screening import MockLayer2
from app.trust.settings import TrustSettings

APPROVAL_SECRET = "test-approval-material-" * 3


def mapped_case(conn, actors, now, *, borrower_ref=None):
    """Explicit administrative fixture for a pre-cutover synthetic case mapping."""
    borrower = UUID(str(borrower_ref)) if borrower_ref else uuid4()
    case_id, account_id = uuid4(), "CF-" + uuid4().hex
    conn.execute(
        "INSERT INTO accounts VALUES (%s,%s,%s,%s,%s,%s)",
        (account_id, "Demo Borrower", "LN-" + account_id, 1250000, now.date(), "Demo Officer"),
    )
    conn.execute(
        "INSERT INTO collection_policies VALUES ('demo-policy-v1',true,8,20,'2027-12-31',1,"
        "'demo_fixture') ON CONFLICT DO NOTHING"
    )
    conn.execute(
        "INSERT INTO collection_cases(account_id,recipient_id,recipient_verified,eligible) "
        "VALUES (%s,%s,true,true)",
        (account_id, "synthetic:" + account_id),
    )
    conn.execute(
        "INSERT INTO collection_authorities VALUES "
        "(%s,'cf','spaylater',%s,'Asia/Jakarta',true,true,false,true,%s,true,%s,%s)",
        (account_id, borrower, now, Jsonb([2026, 2027]), Jsonb([])),
    )
    context = {
        "state": "active",
        "opted_out": False,
        "verified": True,
        "timezone": "Asia/Jakarta",
        "borrower_owned": True,
    }
    conn.execute(
        "INSERT INTO trust.cases(id,principal,product,borrower_ref,is_synthetic,context,facts,"
        "created_at) VALUES (%s,'cf','spaylater',%s,true,%s,%s,%s)",
        (case_id, borrower, Jsonb(context), Jsonb({"borrower_first_name": "Demo Borrower"}), now),
    )
    conn.execute("INSERT INTO legacy_case_account_mappings VALUES (%s,%s)", (case_id, account_id))
    return {"id": str(case_id), "account_id": account_id, "borrower_ref": str(borrower)}


def scenario(conn, actors, now, *, borrower_ref=None):
    document = knowledge.ingest(
        conn,
        actors("knowledge"),
        {
            "title": "Synthetic policy",
            "text": "# Respectful contact\nOffer a human officer for assistance.",
            "source_uri": "synthetic://policy/contact",
            "source_class": "synthetic",
            "source_version": "demo-v1",
            "source_locator": "Respectful contact",
            "effective_from": "2026-01-01T00:00:00Z",
            "criticality": "critical",
            "access_roles": ["knowledge", "qa", "compliance", "operator", "personal"],
        },
        now,
    )
    knowledge.qa_review(conn, actors("qa"), document["id"], now)
    knowledge.approve(conn, actors("compliance"), document["id"], "Synthetic approval", now)
    template = knowledge.create_template(
        conn,
        actors("knowledge"),
        {
            "title": "Reminder",
            "body": "Your {{product_name}} payment is {{amount_due}}. Reply HUMAN for an officer.",
            "document_ids": [document["id"]],
        },
        now,
    )
    knowledge.qa_template(conn, actors("qa"), template["id"], now)
    knowledge.approve_template(
        conn, actors("compliance"), template["id"], "Synthetic approval", now
    )
    data = {"is_synthetic": True}
    if borrower_ref:
        data["borrower_ref"] = str(borrower_ref)
    case = mapped_case(conn, actors, now, borrower_ref=borrower_ref)
    return document, template, case


def prepared(conn, actors, now, template, case, key=None, layer2=None):
    return prepare(
        conn,
        actors("operator"),
        case["id"],
        template["id"],
        str(key or uuid4()),
        APPROVAL_SECRET,
        FrozenClock(now),
        layer2 or MockLayer2(),
    )


def test_inv_delivery_token_and_idempotency(trust_conn, trust_actor, trust_now):
    _, template, case = scenario(trust_conn, trust_actor, trust_now)
    key = str(uuid4())
    first = prepared(trust_conn, trust_actor, trust_now, template, case, key)
    assert first["status"] == "APPROVED"
    result = dispatch(
        trust_conn, trust_actor("operator"), first, APPROVAL_SECRET, FrozenClock(trust_now)
    )
    assert result["status"] == "DELIVERED"
    replay = prepared(trust_conn, trust_actor, trust_now, template, case, key)
    assert replay["idempotent_replay"] is True
    assert (
        trust_conn.execute(
            "SELECT count(*) AS n FROM dispatch_receipts WHERE message_id=%s",
            (first["attempt_id"],),
        ).fetchone()["n"]
        == 1
    )
    assert verify_chain(trust_conn)["valid"] is True


@pytest.mark.parametrize("change", ["token", "text", "recipient", "state", "timezone"])
def test_inv_gate_revalidates(trust_conn, trust_actor, trust_now, change):
    _, template, case = scenario(trust_conn, trust_actor, trust_now)
    draft = prepared(trust_conn, trust_actor, trust_now, template, case)
    if change == "token":
        trust_conn.execute(
            "UPDATE message_approvals SET attestation='invalid' WHERE message_id=%s",
            (draft["attempt_id"],),
        )
    elif change == "text":
        draft["text"] += " altered"
    else:
        field, value = {
            "recipient": ("verified", False),
            "state": ("state", "escalated"),
            "timezone": ("timezone", None),
        }[change]
        from psycopg.types.json import Jsonb

        current = trust_conn.execute(
            "SELECT context FROM trust.cases WHERE id=%s", (case["id"],)
        ).fetchone()["context"]
        current[field] = value
        trust_conn.execute(
            "UPDATE trust.cases SET context=%s WHERE id=%s", (Jsonb(current), case["id"])
        )
    result = dispatch(
        trust_conn, trust_actor("operator"), draft, APPROVAL_SECRET, FrozenClock(trust_now)
    )
    assert result["status"] == "NOT_SENT"
    assert (
        trust_conn.execute(
            "SELECT count(*) AS n FROM dispatch_receipts WHERE message_id=%s",
            (draft["attempt_id"],),
        ).fetchone()["n"]
        == 0
    )


def test_inv_retirement_blocks_previously_approved_attempt(trust_conn, trust_actor, trust_now):
    doc, template, case = scenario(trust_conn, trust_actor, trust_now)
    draft = prepared(trust_conn, trust_actor, trust_now, template, case)
    assert draft["status"] == "APPROVED"
    impact = knowledge.retire(
        trust_conn, trust_actor("compliance"), doc["id"], "Withdraw source", trust_now
    )
    assert template["id"] in impact["suspended_template_ids"]
    result = dispatch(
        trust_conn, trust_actor("operator"), draft, APPROVAL_SECRET, FrozenClock(trust_now)
    )
    assert result["status"] == "NOT_SENT"
    assert verify_chain(trust_conn)["valid"]


def test_inv_guardrail_outage_never_allows(trust_conn, trust_actor, trust_now):
    class BrokenClassifier:
        profile = "injected-failure"

        def evaluate(self, text):
            raise TimeoutError

    _, template, case = scenario(trust_conn, trust_actor, trust_now)
    draft = prepared(trust_conn, trust_actor, trust_now, template, case, layer2=BrokenClassifier())
    assert draft["status"] == "BLOCKED"
    assert not trust_conn.execute(
        "SELECT 1 FROM message_approvals WHERE message_id=%s", (draft["attempt_id"],)
    ).fetchone()


@pytest.mark.parametrize(
    "operation",
    [
        "UPDATE trust.audit_events SET event_type='tampered'",
        "DELETE FROM trust.audit_events",
        "TRUNCATE trust.audit_events",
    ],
)
def test_inv_append_only(trust_conn, trust_actor, trust_now, operation):
    scenario(trust_conn, trust_actor, trust_now)
    with pytest.raises(psycopg.Error), trust_conn.transaction():
        trust_conn.execute(operation)


def test_inv_only_gate_imports_channels():
    violations = []
    for path in Path("app").rglob("*.py"):
        if path.as_posix() == "app/modules/conversations/dispatch.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            if (
                any(
                    "integrations.channels" in module or "services.channels" in module
                    for module in modules
                )
                and "app/integrations/channels/" not in path.as_posix()
            ):
                violations.append(str(path))
    assert violations == []
    issuers = []
    for path in Path("app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "issue_approval"
            ):
                issuers.append(path.as_posix())
    assert sorted(issuers) == [
        "app/modules/compliance/screening.py",
        "app/trust/screening.py",
    ]


def test_new_domain_uses_injected_clock():
    for directory in ("app/trust", "app/services", "app/modules/trust"):
        for path in Path(directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert not (
                        node.func.attr in {"now", "utcnow", "today"}
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in {"datetime", "date"}
                    ), str(path)


def test_inv_concurrent_gate_delivers_once(trust_dsn, trust_actor, trust_now):
    from app.trust.db import initialize

    initialize(trust_dsn)
    with connection(trust_dsn) as conn:
        _, template, case = scenario(conn, trust_actor, trust_now)
        draft = prepared(conn, trust_actor, trust_now, template, case)

    def worker():
        with connection(trust_dsn) as conn:
            return dispatch(
                conn, trust_actor("operator"), draft, APPROVAL_SECRET, FrozenClock(trust_now)
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    assert all(result["status"] == "DELIVERED" for result in results)
    with connection(trust_dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM dispatch_receipts WHERE message_id=%s",
                (draft["attempt_id"],),
            ).fetchone()["n"]
            == 1
        )


def test_retirement_racing_dispatch_has_one_authoritative_order(trust_dsn, trust_actor, trust_now):
    from threading import Barrier

    from app.trust.db import initialize

    initialize(trust_dsn)
    with connection(trust_dsn) as conn:
        document, template, case = scenario(conn, trust_actor, trust_now)
        draft = prepared(conn, trust_actor, trust_now, template, case)
    barrier = Barrier(2)

    def worker(operation):
        with connection(trust_dsn) as conn:
            barrier.wait(timeout=3)
            if operation == "retire":
                return knowledge.retire(
                    conn,
                    trust_actor("compliance"),
                    document["id"],
                    "Concurrent source withdrawal",
                    trust_now,
                )
            return dispatch(
                conn, trust_actor("operator"), draft, APPROVAL_SECRET, FrozenClock(trust_now)
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(worker, ["retire", "dispatch"]))
    assert outcomes[1]["status"] in {"DELIVERED", "NOT_SENT"}
    with connection(trust_dsn) as conn:
        receipt = conn.execute(
            "SELECT id FROM dispatch_receipts WHERE message_id=%s", (draft["attempt_id"],)
        ).fetchone()
        assert bool(receipt) == (outcomes[1]["status"] == "DELIVERED")
        # A fresh candidate cannot use the source after committed retirement.
        if not receipt:
            again = dispatch(
                conn, trust_actor("operator"), draft, APPROVAL_SECRET, FrozenClock(trust_now)
            )
            assert again["status"] == "NOT_SENT"
        assert (
            conn.execute(
                "SELECT status FROM trust.templates WHERE id=%s", (template["id"],)
            ).fetchone()["status"]
            == "suspended"
        )
        assert verify_chain(conn)["valid"] is True


def test_inv_contact_budget_is_per_borrower(trust_conn, trust_actor, trust_now):
    _, template, case = scenario(trust_conn, trust_actor, trust_now)
    draft = prepared(trust_conn, trust_actor, trust_now, template, case)
    assert (
        dispatch(
            trust_conn, trust_actor("operator"), draft, APPROVAL_SECRET, FrozenClock(trust_now)
        )["status"]
        == "DELIVERED"
    )
    second = mapped_case(trust_conn, trust_actor, trust_now, borrower_ref=case["borrower_ref"])
    result = prepared(trust_conn, trust_actor, trust_now, template, second)
    assert result["status"] == "NOT_SENT"


def test_conversation_stays_outside_knowledge_and_pauses(trust_conn, trust_actor, trust_now):
    _, template, case = scenario(trust_conn, trust_actor, trust_now)
    result = receive_conversation(
        trust_conn, trust_actor("operator"), case["id"], "I lost my job and cannot pay", trust_now
    )
    assert result["case_paused"]
    assert prepared(trust_conn, trust_actor, trust_now, template, case)["status"] == "NOT_SENT"
    assert "cannot pay" not in str(
        trust_conn.execute("SELECT payload FROM trust.audit_events").fetchall()
    )


def test_audit_failure_rolls_back_delivery(trust_dsn, trust_actor, trust_now, monkeypatch):
    from app.modules.conversations import dispatch as dispatch_gate
    from app.trust.db import initialize

    initialize(trust_dsn)
    with connection(trust_dsn) as conn:
        _, template, case = scenario(conn, trust_actor, trust_now)
        draft = prepared(conn, trust_actor, trust_now, template, case)
    original = dispatch_gate.append_event

    def fail(*args, **kwargs):
        if args[2] == "simulate_dispatch":
            raise psycopg.OperationalError("injected audit outage")
        return original(*args, **kwargs)

    monkeypatch.setattr(dispatch_gate, "append_event", fail)
    with pytest.raises(psycopg.OperationalError), connection(trust_dsn) as conn:
        dispatch(conn, trust_actor("operator"), draft, APPROVAL_SECRET, FrozenClock(trust_now))
    with connection(trust_dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM dispatch_receipts WHERE message_id=%s",
                (draft["attempt_id"],),
            ).fetchone()["n"]
            == 0
        )


def test_api_auth_validation_and_no_dispatch_route(trust_dsn, trust_now):
    settings = TrustSettings(trust_dsn, "test-jwt-material-" * 3, APPROVAL_SECRET, True)
    with TestClient(create_trust_app(settings, FrozenClock(trust_now))) as client:
        assert client.get("/knowledge/documents").status_code == 401
        token = client.post("/auth/demo-login", json={"role": "personal"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid4())}
        response = client.post("/cases", headers=headers, json={"is_synthetic": True})
        assert response.status_code == 403
        private = "rejected-private-value"
        response = client.post(
            "/cases", headers=headers, json={"is_synthetic": False, "private": private}
        )
        assert response.status_code == 422
        assert private not in response.text
        paths = client.get("/openapi.json").json()["paths"]
        assert all("dispatch" not in path for path in paths)
        assert client.get("/ready").json()["ready"] is True


def test_settings_fail_closed():
    with pytest.raises(ValueError):
        TrustSettings("sqlite://", "short", "short")
    with pytest.raises(ValueError):
        TrustSettings("postgresql://localhost/test", "a" * 48, "b" * 48, simulation_mode=False)


@pytest.mark.parametrize("change", ["hash", "policy", "budget"])
def test_gate_binds_persisted_authority(trust_conn, trust_actor, trust_now, change):
    _, template, case = scenario(trust_conn, trust_actor, trust_now)
    draft = prepared(trust_conn, trust_actor, trust_now, template, case)
    if change == "hash":
        trust_conn.execute(
            "UPDATE message_approvals SET binding=jsonb_set(binding,'{content_hash}',%s) "
            "WHERE message_id=%s",
            (Jsonb("0" * 64), draft["attempt_id"]),
        )
    elif change == "policy":
        trust_conn.execute("UPDATE collection_policies SET version='changed' WHERE active")
    else:
        trust_conn.execute(
            "UPDATE collection_authorities SET mandate_active=false WHERE borrower_ref=%s",
            (case["borrower_ref"],),
        )
    result = dispatch(
        trust_conn, trust_actor("operator"), draft, APPROVAL_SECRET, FrozenClock(trust_now)
    )
    assert result["status"] == "NOT_SENT"


def test_english_duration_cannot_bypass_placeholder_rules():
    from app.trust.errors import TrustError
    from app.trust.safety import render_candidate

    with pytest.raises(TrustError, match="UNSOURCED_NUMBER"):
        render_candidate("You have seven days to pay.", {})


def test_full_api_revocation_loop_and_stale_search_key(trust_dsn, trust_now):
    from app.trust.demo import run_scenario

    settings = TrustSettings(trust_dsn, "test-jwt-material-" * 3, APPROVAL_SECRET, True)
    with TestClient(create_trust_app(settings, FrozenClock(trust_now))) as client:
        report = run_scenario(client)
    assert report["first_attempt"] == "DELIVERED"
    assert report["attempt_after_retirement"] == "NOT_SENT"
    assert not report["retrieved_after_retirement"]


def test_api_body_limit_and_capability_before_replay(trust_dsn, trust_now):
    from app.trust.security import Actor, issue_token

    secret = "test-jwt-material-" * 3
    settings = TrustSettings(trust_dsn, secret, APPROVAL_SECRET, True)
    with TestClient(create_trust_app(settings, FrozenClock(trust_now))) as client:
        oversized = client.post("/cases", content=b"x" * 300001)
        assert oversized.status_code == 413

        def headers(role):
            token = issue_token(Actor("same-user", role, "cf", "spaylater"), secret, trust_now)
            return {"Authorization": "Bearer " + token, "Idempotency-Key": request_key}

        request_key = str(uuid4())
        first = client.post("/cases", json={"is_synthetic": True}, headers=headers("operator"))
        assert first.status_code == 409
        assert first.json()["code"] == "legacy_case_mapping_required"
        replay = client.post("/cases", json={"is_synthetic": True}, headers=headers("personal"))
        assert replay.status_code == 403
