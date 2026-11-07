"""Connection-bound knowledge persistence. The caller owns the transaction."""

from typing import Any

from psycopg.rows import dict_row

from app.modules.knowledge.authorization import Actor
from app.modules.knowledge.processing import _uuid
from app.trust.errors import TrustError


def _rows(conn, query: str, params=()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def _one(conn, query: str, params=()) -> dict:
    rows = _rows(conn, query, params)
    if not rows:
        raise TrustError("NOT_FOUND", status=404)
    return rows[0]


def _document(conn, actor: Actor, document_id: str, *, lock: bool = False) -> dict:
    return _one(
        conn,
        "SELECT * FROM trust.knowledge_documents WHERE id=%s AND principal=%s AND product=%s"
        + (" FOR UPDATE" if lock else ""),
        (_uuid(document_id), actor.principal, actor.product),
    )


def _template(conn, actor: Actor, template_id: str, *, lock: bool = False) -> dict:
    return _one(
        conn,
        "SELECT * FROM trust.templates WHERE id=%s AND principal=%s AND product=%s"
        + (" FOR UPDATE" if lock else ""),
        (_uuid(template_id), actor.principal, actor.product),
    )


def _evidence_ids(conn, template_id: Any) -> list[str]:
    return [
        str(row["document_id"])
        for row in _rows(
            conn,
            "SELECT document_id FROM trust.knowledge_dependencies WHERE template_id=%s ORDER BY document_id",
            (template_id,),
        )
    ]


def lock_governance(conn, parameters=()):
    return conn.execute("SELECT pg_advisory_xact_lock(71107002)", parameters)


def insert_document(conn, parameters=()):
    return _one(
        conn,
        """INSERT INTO trust.knowledge_documents
        (principal,product,title,source_uri,source_class,source_version,source_locator,
         review_status,criticality,access_roles,effective_from,effective_until,text,
         content_hash,created_by,created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        parameters,
    )


def insert_chunk(conn, parameters=()):
    return conn.execute(
        """INSERT INTO trust.knowledge_chunks
            (document_id,ordinal,locator,parent_locator,text,content_hash,embedding)
            VALUES (%s,%s,%s,%s,%s,%s,%s::vector)""",
        parameters,
    )


def record_qa(conn, parameters=()):
    return conn.execute(
        "UPDATE trust.knowledge_documents SET qa_by=%s,qa_hash=%s,qa_at=%s WHERE id=%s", parameters
    )


def approve_document(conn, parameters=()):
    return _one(
        conn,
        """UPDATE trust.knowledge_documents SET status='approved',approved_by=%s,
        approved_hash=content_hash,approved_at=%s WHERE id=%s RETURNING *""",
        parameters,
    )


def retire_document(conn, parameters=()):
    return conn.execute(
        "UPDATE trust.knowledge_documents SET status='retired',retired_at=%s WHERE id=%s",
        parameters,
    )


def suspend_dependencies(conn, parameters=()):
    return _rows(
        conn,
        """UPDATE trust.templates SET status='suspended',suspended_at=%s
        WHERE id IN (SELECT template_id FROM trust.knowledge_dependencies WHERE document_id=%s)
        AND principal=%s AND product=%s AND status<>'suspended' RETURNING id""",
        parameters,
    )


def lock_current_document(conn, parameters=()):
    return _one(
        conn,
        "SELECT * FROM trust.knowledge_documents WHERE id=%s AND principal=%s AND product=%s FOR SHARE",
        parameters,
    )


def insert_template(conn, parameters=()):
    return _one(
        conn,
        """INSERT INTO trust.templates
        (principal,product,title,body,content_hash,created_by,created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        parameters,
    )


def insert_dependency(conn, parameters=()):
    return conn.execute(
        """INSERT INTO trust.knowledge_dependencies
            (document_id,template_id,principal,product,weight) VALUES (%s,%s,%s,%s,%s)""",
        parameters,
    )


def record_template_qa(conn, parameters=()):
    return conn.execute("UPDATE trust.templates SET qa_by=%s,qa_hash=%s WHERE id=%s", parameters)


def approve_template(conn, parameters=()):
    return _one(
        conn,
        """UPDATE trust.templates SET status='approved',approved_by=%s,
        approved_hash=content_hash,approved_at=%s WHERE id=%s RETURNING *""",
        parameters,
    )


def search_chunks(conn, parameters=()):
    # Manager can read all published knowledge within its authenticated scope.
    # The remaining roles still require the document's explicit access grant.
    return _rows(
        conn,
        """WITH allowed AS MATERIALIZED (
        SELECT c.id,c.document_id,c.locator,c.parent_locator,c.text,c.search_vector,c.embedding,
        d.title,d.source_uri,d.source_version,d.source_locator,d.source_class,d.review_status,
        d.criticality,d.content_hash AS document_hash,c.content_hash AS snippet_hash,
        d.approved_at,d.effective_from,d.effective_until
        FROM trust.knowledge_chunks c JOIN trust.knowledge_documents d ON d.id=c.document_id
        WHERE d.principal=%s AND d.product=%s AND d.status='approved'
          AND d.effective_from<=%s AND (d.effective_until IS NULL OR d.effective_until>%s)
          AND %s=ANY(array_append(d.access_roles, 'manager'))
          AND d.qa_hash=d.content_hash AND d.approved_hash=d.content_hash
    ), lexical AS (
        SELECT id,row_number() OVER (ORDER BY ts_rank_cd(search_vector,
            plainto_tsquery('english',%s)) DESC,id) AS rank
        FROM allowed WHERE search_vector @@ plainto_tsquery('english',%s) LIMIT 30
    ), projected AS (
        SELECT id,row_number() OVER (ORDER BY embedding <=> %s::vector,id) AS rank
        FROM allowed ORDER BY embedding <=> %s::vector,id LIMIT 30
    ), scores AS (
        SELECT id,sum(score) AS score FROM (
          SELECT id,1.0/(60+rank) AS score FROM lexical
          UNION ALL SELECT id,1.0/(60+rank) AS score FROM projected
        ) ranked GROUP BY id
    ) SELECT a.id,a.document_id,a.locator,a.parent_locator,a.text,a.title,a.source_uri,
       a.source_version,a.source_locator,a.source_class,a.review_status,a.criticality,
       a.document_hash,a.snippet_hash,a.approved_at,a.effective_from,a.effective_until,s.score FROM allowed a JOIN scores s ON a.id=s.id
       ORDER BY s.score DESC,a.id LIMIT %s""",
        parameters,
    )


def expand_critical(conn, parameters=()):
    return _rows(
        conn,
        """SELECT c.id,c.document_id,c.locator,c.parent_locator,
            c.text,c.content_hash,d.source_version,d.text AS source_text,d.content_hash AS source_hash
            FROM trust.knowledge_chunks c JOIN trust.knowledge_documents d ON d.id=c.document_id
            WHERE c.document_id=ANY(%s::uuid[]) ORDER BY c.document_id,c.ordinal""",
        parameters,
    )


def list_documents(conn, parameters=()):
    return _rows(
        conn,
        """SELECT id,title,status,source_class,source_version,review_status,
        criticality,effective_from,effective_until,content_hash FROM trust.knowledge_documents
        WHERE principal=%s AND product=%s AND %s=ANY(access_roles)
        AND (status='approved' OR %s) ORDER BY created_at,id LIMIT %s OFFSET %s""",
        parameters,
    )


def list_templates(conn, parameters=()):
    return _rows(
        conn,
        """SELECT id,title,status,content_hash FROM trust.templates
        WHERE principal=%s AND product=%s ORDER BY created_at,id LIMIT %s OFFSET %s""",
        parameters,
    )


def dependency_graph(conn, parameters=()):
    return _rows(
        conn,
        """SELECT e.document_id,e.template_id,e.relation,e.weight,
        d.status AS document_status,t.status AS template_status
        FROM trust.knowledge_dependencies e JOIN trust.knowledge_documents d ON d.id=e.document_id
        JOIN trust.templates t ON t.id=e.template_id
        WHERE e.principal=%s AND e.product=%s AND %s=ANY(d.access_roles)
        ORDER BY e.document_id,e.template_id LIMIT %s OFFSET %s""",
        parameters,
    )


def reference_chunk(conn, actor, document_id, chunk_id):
    rows = _rows(
        conn,
        "SELECT c.id AS chunk_id,c.document_id,c.text,c.content_hash AS snippet_hash,"
        "d.title,d.source_uri,d.source_version,d.content_hash AS document_hash,d.status,"
        "d.approved_hash,d.qa_hash,d.approved_at,d.effective_from,d.effective_until,d.access_roles "
        "FROM trust.knowledge_chunks c JOIN trust.knowledge_documents d ON d.id=c.document_id "
        "WHERE d.id=%s AND c.id=%s AND d.principal=%s AND d.product=%s FOR SHARE OF d,c",
        (document_id, chunk_id, actor.principal, actor.product),
    )
    return rows[0] if rows else None


def locked_template(conn, actor, template_id):
    return _one(
        conn,
        "SELECT * FROM trust.templates WHERE id=%s AND principal=%s AND product=%s FOR SHARE",
        (_uuid(template_id), actor.principal, actor.product),
    )


def template_source_chunks(conn, actor, document_ids):
    return _rows(
        conn,
        "SELECT DISTINCT ON (d.id) c.id,c.document_id,c.locator,c.parent_locator,c.text,"
        "c.content_hash AS snippet_hash,d.title,d.source_uri,d.source_version,d.source_locator,"
        "d.source_class,d.review_status,d.criticality,d.content_hash AS document_hash,"
        "d.approved_at,d.effective_from,d.effective_until "
        "FROM trust.knowledge_documents d JOIN trust.knowledge_chunks c ON c.document_id=d.id "
        "WHERE d.id=ANY(%s::uuid[]) AND d.principal=%s AND d.product=%s "
        "AND %s=ANY(d.access_roles) ORDER BY d.id,c.ordinal,c.id",
        (document_ids, actor.principal, actor.product, actor.role),
    )
