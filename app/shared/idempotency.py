import hashlib
import json

from psycopg.types.json import Jsonb

from app.shared.errors import DomainError


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def replay(connection, scope: str, key: str, payload: dict):
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (scope + key,))
    row = connection.execute(
        "SELECT * FROM collection_requests WHERE scope=%s AND key=%s", (scope, key)
    ).fetchone()
    if row:
        if row["payload_hash"] != digest(payload):
            raise DomainError("idempotency_conflict", "Key was already used for another payload")
        return row["response"]
    return None


def record(connection, scope: str, key: str, payload: dict, response: dict):
    connection.execute(
        "INSERT INTO collection_requests(scope,key,payload_hash,response) VALUES (%s,%s,%s,%s)",
        (scope, key, digest(payload), Jsonb(response)),
    )
