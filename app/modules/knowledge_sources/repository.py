"""Immutable source storage; caller owns transaction and authorization."""

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.trust.security import Actor

ConnectionType = Connection[dict[str, Any]]

SUMMARY = """SELECT r.id,r.source_key,r.title,r.publisher,r.source_url,r.final_url,
    r.source_version,r.source_class,r.artifact_sha256,r.legal_status,r.effective_from,
    r.effective_until,r.created_at,a.page_count,octet_length(a.payload) AS byte_size,
    r.extraction_method,r.extraction_review_required,
    (SELECT count(*) FROM trust.source_provisions p WHERE p.source_id=r.id) AS provision_count
    FROM trust.source_records r JOIN trust.source_artifacts a ON a.sha256=r.artifact_sha256"""


def lock_archive(conn: ConnectionType) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(71107002)")


def existing(conn: ConnectionType, who: Actor, key: str, digest: str, manifest: str) -> dict | None:
    return conn.execute(
        "SELECT id FROM trust.source_records WHERE principal=%s AND product=%s "
        "AND source_key=%s AND artifact_sha256=%s AND manifest_sha256=%s",
        (who.principal, who.product, key, digest, manifest),
    ).fetchone()


def save_artifact(conn: ConnectionType, digest: str, body: bytes, pages: int, now: Any) -> None:
    conn.execute(
        "INSERT INTO trust.source_artifacts (sha256,payload,content_type,page_count,stored_at) "
        "VALUES (%s,%s,'application/pdf',%s,%s) ON CONFLICT (sha256) DO NOTHING",
        (digest, body, pages, now),
    )


def save_record(conn: ConnectionType, values: dict[str, Any]) -> str:
    names = tuple(values)
    # Names originate exclusively from the service, never an HTTP or manifest field.
    from psycopg import sql

    row = conn.execute(
        sql.SQL("INSERT INTO trust.source_records ({}) VALUES ({}) RETURNING id").format(
            sql.SQL(",").join(map(sql.Identifier, names)),
            sql.SQL(",").join(sql.Placeholder() for _ in names),
        ),
        tuple(
            Jsonb(value) if isinstance(value, (dict, list)) else value for value in values.values()
        ),
    ).fetchone()
    assert row is not None
    return str(row["id"])


def save_provision(conn: ConnectionType, values: dict[str, Any]) -> None:
    from psycopg import sql

    conn.execute(
        sql.SQL("INSERT INTO trust.source_provisions ({}) VALUES ({})").format(
            sql.SQL(",").join(map(sql.Identifier, values)),
            sql.SQL(",").join(sql.Placeholder() for _ in values),
        ),
        tuple(
            Jsonb(value) if isinstance(value, (dict, list)) else value for value in values.values()
        ),
    )


def list_records(conn: ConnectionType, who: Actor, limit: int, offset: int) -> dict[str, Any]:
    scope = (who.principal, who.product)
    count = conn.execute(
        "SELECT count(*) AS total FROM trust.source_records WHERE principal=%s AND product=%s",
        scope,
    ).fetchone()
    assert count is not None
    items = conn.execute(
        SUMMARY
        + " WHERE r.principal=%s AND r.product=%s ORDER BY r.source_key,r.id LIMIT %s OFFSET %s",
        (*scope, limit, offset),
    ).fetchall()
    return {"items": items, "total": count["total"], "limit": limit, "offset": offset}


def detail(conn: ConnectionType, who: Actor, source_id: str) -> dict | None:
    row = conn.execute(
        SUMMARY + " WHERE r.id=%s AND r.principal=%s AND r.product=%s",
        (source_id, who.principal, who.product),
    ).fetchone()
    if row is None:
        return None
    metadata = conn.execute(
        "SELECT metadata FROM trust.source_records WHERE id=%s", (source_id,)
    ).fetchone()
    assert metadata is not None
    row["metadata"] = metadata["metadata"]
    row["provisions"] = conn.execute(
        """SELECT p.id,p.provision_key,p.locator,p.page_start,p.page_end,p.knowledge_document_id,
        p.stage_status,p.reason_code,p.text_sha256,d.status AS knowledge_status,p.metadata
        FROM trust.source_provisions p LEFT JOIN trust.knowledge_documents d
        ON d.id=p.knowledge_document_id WHERE p.source_id=%s ORDER BY p.page_start,p.provision_key""",
        (source_id,),
    ).fetchall()
    return row


def download(conn: ConnectionType, who: Actor, source_id: str) -> dict | None:
    return conn.execute(
        """SELECT a.payload,a.sha256 FROM trust.source_records r JOIN trust.source_artifacts a
        ON a.sha256=r.artifact_sha256 WHERE r.id=%s AND r.principal=%s AND r.product=%s""",
        (source_id, who.principal, who.product),
    ).fetchone()
