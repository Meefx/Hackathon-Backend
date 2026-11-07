from uuid import uuid4

from psycopg.types.json import Jsonb

from app.shared.idempotency import digest


def append_event(
    connection, account_id, action, actor_kind, outcome, now, run_id=None, reason_codes=None,
    *, correlation_id: str | None = None,
):
    # Account lock serializes all account effects and the append-only hash chain.
    connection.execute("SELECT id FROM accounts WHERE id=%s FOR UPDATE", (account_id,))
    previous = connection.execute(
        "SELECT event_hash FROM audit_events WHERE account_id=%s ORDER BY sequence DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    event = {
        "id": str(uuid4()),
        "account_id": account_id,
        "run_id": run_id,
        "action": action,
        "actor_kind": actor_kind,
        "outcome": outcome,
        "reason_codes": reason_codes or [],
        "correlation_id": correlation_id or str(uuid4()),
        "created_at": now.isoformat(),
        "previous_hash": previous["event_hash"] if previous else None,
    }
    event["event_hash"] = digest(event)
    connection.execute(
        "INSERT INTO audit_events(id,account_id,run_id,action,actor_kind,outcome,"
        "reason_codes,correlation_id,created_at,previous_hash,event_hash) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            event["id"],
            account_id,
            run_id,
            action,
            actor_kind,
            outcome,
            Jsonb(event["reason_codes"]),
            event["correlation_id"],
            now,
            event["previous_hash"],
            event["event_hash"],
        ),
    )
    return event


def verify_chain(events):
    previous = None
    for source in events:
        event = {k: v for k, v in source.items() if k not in ("event_hash", "sequence")}
        if hasattr(event["created_at"], "isoformat"):
            event["created_at"] = event["created_at"].isoformat()
        if event["previous_hash"] != previous or digest(event) != source["event_hash"]:
            return False
        previous = source["event_hash"]
    return True
