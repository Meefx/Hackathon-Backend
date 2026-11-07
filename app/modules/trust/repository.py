"""Persistence primitives for transaction-level API idempotency and audit reads."""

from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.trust.security import Actor


def lock_audit(conn: Connection[dict[str, Any]]) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")


def lock_write(conn: Connection[dict[str, Any]], actor: Actor, key: UUID, route: str) -> None:
    conn.execute("SET LOCAL statement_timeout='5s'")
    conn.execute("SET LOCAL lock_timeout='3s'")
    # Every modifying trust transaction takes the global audit lock first.
    lock_audit(conn)
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (actor.principal + actor.product + str(key) + route,),
    )


def previous_request(
    conn: Connection[dict[str, Any]], actor: Actor, key: UUID, route: str
) -> dict | None:
    return conn.execute(
        """SELECT * FROM trust.request_records
        WHERE principal=%s AND product=%s AND key=%s AND route=%s""",
        (actor.principal, actor.product, key, route),
    ).fetchone()


def save_request(
    conn: Connection[dict[str, Any]],
    actor: Actor,
    key: UUID,
    route: str,
    request_hash: str,
    result: dict[str, Any],
) -> None:
    conn.execute(
        "INSERT INTO trust.request_records VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (actor.principal, actor.product, key, route, actor.subject, request_hash, Jsonb(result)),
    )


def audit_events(conn: Connection[dict[str, Any]], actor: Actor) -> list[dict]:
    return conn.execute(
        """SELECT * FROM trust.audit_events WHERE principal=%s AND product=%s
        ORDER BY seq DESC LIMIT 100""",
        (actor.principal, actor.product),
    ).fetchall()
