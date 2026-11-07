"""Single delivery boundary. Every check reads current authoritative state."""

import hashlib
from uuid import UUID

from psycopg.types.json import Jsonb

from app.services.channels.simulated import deliver
from app.trust.audit import append_event
from app.trust.errors import TrustError
from app.trust.knowledge import get_template
from app.trust.runtime import contact_context, scoped_case
from app.trust.safety import evaluate_contact, verify_approval
from app.trust.security import require


def dispatch(conn, actor, prepared, secret, clock):
    # Service callers must satisfy the same capability as the HTTP pipeline,
    # including idempotent replays. An existing approval does not grant a role.
    require(actor, "attempt:create")
    # Global audit chain has one writer in this MVP. Take its lock first,
    # consistently with knowledge lifecycle mutations, to avoid lock inversions.
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    attempt = conn.execute(
        """SELECT * FROM trust.attempts WHERE id=%s AND principal=%s AND product=%s FOR UPDATE""",
        (UUID(prepared["attempt_id"]), actor.principal, actor.product),
    ).fetchone()
    if attempt is None:
        raise TrustError("ATTEMPT_NOT_FOUND", 404)
    if attempt["status"] == "DELIVERED":
        return {
            "attempt_id": str(attempt["id"]),
            "status": "DELIVERED",
            "is_simulated": True,
            "idempotent_replay": True,
        }
    now = clock.now()
    try:
        if attempt["status"] != "APPROVED":
            raise TrustError("ATTEMPT_NOT_APPROVED")
        # Same lock order as retirement: documents, template, case, contact budget.
        for item in sorted(attempt["evidence"], key=lambda value: value["id"]):
            conn.execute(
                "SELECT id FROM trust.knowledge_documents WHERE id=%s FOR SHARE",
                (UUID(item["id"]),),
            ).fetchone()
        conn.execute(
            "SELECT id FROM trust.templates WHERE id=%s FOR SHARE", (attempt["template_id"],)
        ).fetchone()
        case = scoped_case(conn, actor, str(attempt["case_id"]), lock=True)
        budget = conn.execute(
            """SELECT borrower_ref FROM trust.contact_budgets
            WHERE principal=%s AND product=%s AND borrower_ref=%s FOR UPDATE""",
            (actor.principal, actor.product, case["borrower_ref"]),
        ).fetchone()
        if not budget:
            raise TrustError("CONTACT_BUDGET_MISSING")
        # All potentially blocking authority locks are now held. A previous
        # approval does not freeze legal time, token expiry or source validity.
        now = clock.now()
        template = get_template(conn, actor, str(attempt["template_id"]), now)
        if template["evidence"] != attempt["evidence"]:
            raise TrustError("EVIDENCE_CHANGED")
        eligibility = evaluate_contact(contact_context(conn, actor, case), now)
        append_event(
            conn,
            actor,
            "eligibility.dispatch",
            {
                "attempt_id": str(attempt["id"]),
                "allowed": eligibility["allowed"],
                "reason_codes": eligibility["reason_codes"],
            },
            now,
        )
        if not eligibility["allowed"]:
            raise TrustError(eligibility["reason_codes"][0])
        if case["version"] != attempt["case_version"]:
            raise TrustError("CASE_VERSION_CHANGED")
        if hashlib.sha256(prepared["text"].encode()).hexdigest() != attempt["text_sha256"]:
            raise TrustError("TEXT_HASH_MISMATCH")
        verify_approval(
            prepared.get("token"),
            secret,
            str(attempt["id"]),
            str(case["id"]),
            prepared["text"],
            attempt["evidence"],
            now,
            policy_version=attempt["policy_version"],
        )
    except TrustError as exc:
        conn.execute(
            "UPDATE trust.attempts SET status='NOT_SENT',decision=%s WHERE id=%s",
            (Jsonb({"reason_codes": [exc.code]}), attempt["id"]),
        )
        append_event(
            conn,
            actor,
            "dispatch.rejected",
            {
                "attempt_id": str(attempt["id"]),
                "reason_codes": [exc.code],
            },
            now,
        )
        return {
            "attempt_id": str(attempt["id"]),
            "status": "NOT_SENT",
            "reason_codes": [exc.code],
            "is_simulated": True,
        }
    # Both audit and simulated delivery commit atomically or neither exists.
    append_event(
        conn,
        actor,
        "dispatch.authorized",
        {
            "attempt_id": str(attempt["id"]),
            "text_sha256": attempt["text_sha256"],
        },
        now,
    )
    dispatch_id = deliver(conn, actor, attempt, case, prepared["text"], now)
    conn.execute("UPDATE trust.attempts SET status='DELIVERED' WHERE id=%s", (attempt["id"],))
    append_event(
        conn,
        actor,
        "dispatch.delivered",
        {
            "attempt_id": str(attempt["id"]),
            "dispatch_id": dispatch_id,
            "text_sha256": attempt["text_sha256"],
            "is_simulated": True,
        },
        now,
    )
    return {
        "attempt_id": str(attempt["id"]),
        "dispatch_id": dispatch_id,
        "status": "DELIVERED",
        "is_simulated": True,
        "message": prepared["text"],
    }
