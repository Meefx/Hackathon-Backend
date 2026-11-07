"""Synthetic operational facts remain separate from reusable knowledge."""

import hashlib
import re
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.trust.audit import append_event
from app.trust.errors import TrustError
from app.trust.processing import sanitize_conversation
from app.trust.security import require

HOLIDAYS = [
    "2026-01-01",
    "2026-01-16",
    "2026-02-17",
    "2026-03-19",
    "2026-03-21",
    "2026-03-22",
    "2026-04-03",
    "2026-04-05",
    "2026-05-01",
    "2026-05-14",
    "2026-05-27",
    "2026-05-31",
    "2026-06-01",
    "2026-06-16",
    "2026-08-17",
    "2026-08-25",
    "2026-12-25",
    "2026-02-16",
    "2026-03-18",
    "2026-03-20",
    "2026-03-23",
    "2026-03-24",
    "2026-05-15",
    "2026-05-28",
    "2026-12-24",
    "2027-01-01",
    "2027-01-05",
    "2027-02-06",
    "2027-03-08",
    "2027-03-10",
    "2027-03-11",
    "2027-03-26",
    "2027-03-28",
    "2027-05-01",
    "2027-05-06",
    "2027-05-17",
    "2027-05-20",
    "2027-06-01",
    "2027-06-06",
    "2027-08-15",
    "2027-08-17",
    "2027-12-25",
    "2027-12-26",
    "2027-02-05",
    "2027-03-09",
    "2027-03-12",
    "2027-03-15",
    "2027-03-25",
    "2027-05-18",
    "2027-05-19",
    "2027-12-24",
]


def scoped_case(conn, actor, case_id, *, lock=False):
    try:
        value = UUID(str(case_id))
    except ValueError as exc:
        raise TrustError("INVALID_CASE_ID", 422) from exc
    row = conn.execute(
        "SELECT * FROM trust.cases WHERE id=%s AND principal=%s AND product=%s"
        + (" FOR UPDATE" if lock else ""),
        (value, actor.principal, actor.product),
    ).fetchone()
    if row is None:
        raise TrustError("CASE_NOT_FOUND", 404)
    return row


def create_case(conn, actor, data, now):
    require(actor, "case:create")
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    if data.get("is_synthetic") is not True:
        raise TrustError("SYNTHETIC_ONLY", 422)
    case_id, borrower_ref = uuid4(), UUID(data.get("borrower_ref") or str(uuid4()))
    context = {
        "is_synthetic": True,
        "timezone": data.get("timezone", "Asia/Jakarta"),
        "state": "active",
        "purpose": "proactive",
        "borrower_owned": True,
        "verified": True,
        "opted_out": False,
        "channel": "sim_chat",
        "data_verified_at": now.isoformat(),
        "calendar_years": [2026, 2027],
        "holidays": HOLIDAYS,
        "proactive_history": [],
    }
    facts = {
        "borrower_first_name": "Demo Borrower",
        "lender_name": "Synthetic Lender",
        "product_name": "Demo Credit",
        "amount_due": "Rp1.250.000",
        "due_date": "1 November 2026",
        "payment_instructions": "the official application",
    }
    conn.execute(
        """INSERT INTO trust.cases
        (id,principal,product,borrower_ref,is_synthetic,context,facts,created_at)
        VALUES (%s,%s,%s,%s,true,%s,%s,%s)""",
        (case_id, actor.principal, actor.product, borrower_ref, Jsonb(context), Jsonb(facts), now),
    )
    conn.execute(
        "INSERT INTO trust.contact_budgets VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
        (actor.principal, actor.product, borrower_ref),
    )
    append_event(conn, actor, "case.created", {"case_id": str(case_id)}, now)
    return {"id": str(case_id), "borrower_ref": str(borrower_ref), "is_synthetic": True}


def contact_context(conn, actor, case):
    history = conn.execute(
        """SELECT delivered_at FROM trust.dispatches
        WHERE principal=%s AND product=%s AND borrower_ref=%s AND purpose='proactive'""",
        (actor.principal, actor.product, case["borrower_ref"]),
    ).fetchall()
    return {
        **case["context"],
        "proactive_history": [r["delivered_at"].isoformat() for r in history],
    }


def receive_conversation(conn, actor, case_id, text, now):
    require(actor, "conversation:write")
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    case = scoped_case(conn, actor, case_id, lock=True)
    if not isinstance(text, str) or not 1 <= len(text) <= 1000:
        raise TrustError("INVALID_MESSAGE", 422)
    # Known safety facts are structured outside any LLM summary.
    patterns = {
        "third_party_responder": r"\b(wife|husband|not the borrower|wrong number|istrinya|suaminya|bukan orangnya)\b",
        "stop_request": r"\b(stop|unsubscribe|do not contact|jangan hubungi|berhenti)\b",
        "dispute": r"\b(not my debt|already paid|wrong amount|fraud|bukan utang|sudah bayar|sudah lunas)\b",
        "hardship": r"\b(lost my job|cannot pay|hospital|bereavement|phk|rumah sakit|tidak ada uang)\b",
        "human_request": r"\b(human|officer|petugas)\b",
        "distress": r"\b(kill myself|suicide|want to die|lebih baik mati|bunuh diri)\b",
    }
    categories = sorted(
        k for k, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)
    )
    if categories:
        context = {**case["context"], "state": "escalated"}
        if "stop_request" in categories or "third_party_responder" in categories:
            context["opted_out"] = True
        conn.execute(
            "UPDATE trust.cases SET context=%s,version=version+1 WHERE id=%s",
            (Jsonb(context), case["id"]),
        )
    message_id = uuid4()
    redacted = sanitize_conversation(text, identifiers=(case["facts"]["borrower_first_name"],))
    conn.execute(
        """INSERT INTO trust.conversations
        (id,principal,product,case_id,body,context_redacted,categories,is_synthetic,created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,true,%s)""",
        (message_id, actor.principal, actor.product, case["id"], text, redacted, categories, now),
    )
    append_event(
        conn,
        actor,
        "conversation.received",
        {
            "case_id": str(case["id"]),
            "message_id": str(message_id),
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "categories": categories,
        },
        now,
    )
    return {
        "id": str(message_id),
        "categories": categories,
        "case_paused": bool(categories),
        "is_synthetic": True,
    }


def read_conversations(conn, actor, case_id):
    require(actor, "conversation:read")
    scoped_case(conn, actor, case_id)
    return conn.execute(
        """SELECT id,body,context_redacted,categories,created_at FROM trust.conversations
        WHERE case_id=%s AND principal=%s AND product=%s ORDER BY created_at LIMIT 100""",
        (UUID(str(case_id)), actor.principal, actor.product),
    ).fetchall()
