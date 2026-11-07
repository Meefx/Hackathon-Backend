"""Serialised, append-only evidence. Payloads contain IDs and decisions only."""

import hashlib
import json
from datetime import UTC
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.trust.errors import TrustError

FORBIDDEN_FIELDS = {"text", "body", "query", "note", "token", "secret", "transcript", "facts"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _safe(value):
    if isinstance(value, dict):
        if FORBIDDEN_FIELDS.intersection(value):
            raise TrustError("AUDIT_PAYLOAD_REJECTED", 500)
        for child in value.values():
            _safe(child)
    elif isinstance(value, list):
        for child in value:
            _safe(child)


def _event(row):
    return {
        key: (
            row[key].astimezone(UTC).isoformat().replace("+00:00", "Z")
            if key == "occurred_at"
            else str(row[key])
            if key == "event_id"
            else row[key]
        )
        for key in (
            "seq",
            "event_id",
            "occurred_at",
            "actor_id",
            "actor_role",
            "principal",
            "product",
            "event_type",
            "payload",
        )
    }


def append_event(conn, actor, event_type, payload, now):
    _safe(payload)
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")
    previous = conn.execute(
        "SELECT seq, hash FROM trust.audit_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = previous["hash"] if previous else "0" * 64
    row = {
        "seq": previous["seq"] + 1 if previous else 1,
        "event_id": uuid4(),
        "occurred_at": now,
        "actor_id": actor.subject,
        "actor_role": actor.role,
        "principal": actor.principal,
        "product": actor.product,
        "event_type": event_type,
        "payload": payload,
    }
    digest = hashlib.sha256((prev_hash + canonical(_event(row))).encode()).hexdigest()
    conn.execute(
        """INSERT INTO trust.audit_events
        (seq,event_id,occurred_at,actor_id,actor_role,principal,product,event_type,payload,prev_hash,hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            row["seq"],
            row["event_id"],
            now,
            actor.subject,
            actor.role,
            actor.principal,
            actor.product,
            event_type,
            Jsonb(payload),
            prev_hash,
            digest,
        ),
    )
    return str(row["event_id"])


def verify_chain(conn):
    previous = "0" * 64
    checked = 0
    for row in conn.execute("SELECT * FROM trust.audit_events ORDER BY seq"):
        checked += 1
        digest = hashlib.sha256((previous + canonical(_event(row))).encode()).hexdigest()
        if row["seq"] != checked or row["prev_hash"] != previous or row["hash"] != digest:
            return {"valid": False, "checked": checked, "first_broken_seq": row["seq"]}
        previous = row["hash"]
    return {"valid": True, "checked": checked, "first_broken_seq": None, "head_hash": previous}
