"""One explicit attempt pipeline. Tokens stay in memory and never reach clients."""

import hashlib
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.trust.audit import append_event, canonical
from app.trust.errors import TrustError
from app.trust.knowledge import get_template
from app.trust.runtime import contact_context, scoped_case
from app.trust.safety import evaluate_contact, render_candidate
from app.trust.screening import approve_candidate
from app.trust.security import require


def prepare(conn, actor, case_id, template_id, idempotency_key, secret, clock, layer2):
    require(actor, "attempt:create")
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    now = clock.now()
    case_id, template_id, key = (
        UUID(str(case_id)),
        UUID(str(template_id)),
        UUID(str(idempotency_key)),
    )
    request_hash = hashlib.sha256(
        canonical({"case_id": str(case_id), "template_id": str(template_id)}).encode()
    ).hexdigest()
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (actor.principal + actor.product + str(key),),
    )
    existing = conn.execute(
        """SELECT * FROM trust.attempts
        WHERE principal=%s AND product=%s AND idempotency_key=%s""",
        (actor.principal, actor.product, key),
    ).fetchone()
    if existing:
        if existing["request_hash"] != request_hash:
            raise TrustError("IDEMPOTENCY_MISMATCH")
        append_event(conn, actor, "idempotency.replayed", {"attempt_id": str(existing["id"])}, now)
        return {
            "attempt_id": str(existing["id"]),
            "status": existing["status"],
            "idempotent_replay": True,
            "is_simulated": True,
        }
    case = scoped_case(conn, actor, str(case_id))
    attempt_id = str(uuid4())
    decision = evaluate_contact(contact_context(conn, actor, case), now)
    append_event(
        conn,
        actor,
        "eligibility.pre_generation",
        {
            "attempt_id": attempt_id,
            "allowed": decision["allowed"],
            "reason_codes": decision["reason_codes"],
        },
        now,
    )
    evidence, text, token = [], None, None
    status = "NOT_SENT"
    if decision["allowed"]:
        try:
            template = get_template(conn, actor, str(template_id), now)
            evidence = template["evidence"]
            text = render_candidate(template["body"], case["facts"])
            # Disclosure is server controlled, irrespective of generated/template copy.
            delivered = conn.execute(
                "SELECT 1 FROM trust.dispatches WHERE case_id=%s LIMIT 1", (case_id,)
            ).fetchone()
            if not delivered and "automated assistant" not in text.casefold():
                text = "I am an automated assistant for Synthetic Lender. " + text
            decision, token = approve_candidate(
                secret=secret,
                attempt_id=attempt_id,
                case_id=str(case_id),
                text=text,
                evidence=evidence,
                now=now,
                layer2=layer2,
            )
            status = "APPROVED" if token else "BLOCKED"
        except TrustError as exc:
            decision = {"reason_codes": [exc.code]}
    text_hash = hashlib.sha256(text.encode()).hexdigest() if text else None
    conn.execute(
        """INSERT INTO trust.attempts
        (id,principal,product,case_id,template_id,idempotency_key,request_hash,case_version,status,
         text_sha256,evidence,decision,policy_version,created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'collections-policy-v1',%s)""",
        (
            UUID(attempt_id),
            actor.principal,
            actor.product,
            case_id,
            template_id,
            key,
            request_hash,
            case["version"],
            status,
            text_hash,
            Jsonb(evidence),
            Jsonb(decision),
            now,
        ),
    )
    append_event(
        conn,
        actor,
        "guardrail.decided",
        {
            "attempt_id": attempt_id,
            "status": status,
            "text_sha256": text_hash,
            "reason_codes": decision.get("reason_codes", []),
            "document_ids": [item["id"] for item in evidence],
            "classifier_profile": layer2.profile,
        },
        now,
    )
    return {
        "attempt_id": attempt_id,
        "status": status,
        "text": text,
        "token": token,
        "decision": decision,
        "is_simulated": True,
    }


def execute(conn, actor, case_id, template_id, key, secret, clock, layer2):
    from app.services.dispatch_gate import dispatch

    result = prepare(conn, actor, case_id, template_id, key, secret, clock, layer2)
    if result["status"] == "APPROVED" and not result.get("idempotent_replay"):
        result = dispatch(conn, actor, result, secret, clock)
    return {k: v for k, v in result.items() if k not in {"token", "text"}}
