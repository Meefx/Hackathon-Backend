"""Original regulatory PDFs remain immutable, scoped and unapproved after staging."""

import hashlib
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import psycopg
import pytest
from alembic import command

from app.config import TrustSettings
from app.core.clock import FrozenClock
from app.db.connection import REQUIRED_REVISION, connect, create_pool
from app.integrations.knowledge_sources.extract import extract_pdf
from app.modules.knowledge_sources.preparation import PreparedSource, pages_hash
from app.modules.knowledge_sources.schemas import SourceSpec
from app.trust import knowledge
from app.trust.errors import TrustError
from tests.conftest import migration_config
from tests.test_knowledge import approved_document
from tests.test_source_extract import make_pdf


def prepared_source(now, *, text=None, review_required=False, legal_status="in_force", **changes):
    text = text or "Pasal 62\nContact only the verified borrower.\nPasal 63\nSeparate provision."
    body = make_pdf([text])
    pages = extract_pdf(body).pages
    spec = SourceSpec.model_validate(
        {
            "key": "synthetic-official-source",
            "title": "Synthetic public regulation for archive tests",
            "publisher": "Synthetic official publisher",
            "source_class": "primary",
            "source_url": "https://www.ojk.go.id/synthetic-archive-test.pdf",
            "source_version": "synthetic-test-v1",
            "effective_from": (now - timedelta(days=1)).isoformat(),
            "legal_status": legal_status,
            "scopes": [{"principal": "cf", "product": "spaylater"}],
            "sha256": hashlib.sha256(body).hexdigest(),
            "extraction_sha256": pages_hash(pages),
            "provisions": [
                {
                    "key": "article-62",
                    "locator": "Article 62, synthetic test only",
                    "title": "Synthetic borrower-only control",
                    "article": 62,
                    "page_start": 1,
                    "page_end": 1,
                    "section": "body",
                    "effective_from": (now - timedelta(days=1)).isoformat(),
                    "legal_status": legal_status,
                }
            ],
        }
    )
    result = PreparedSource(
        spec, body, spec.source_url, pages, pages_hash(pages), "pypdf", review_required
    )
    return replace(result, **changes)


@pytest.fixture
def source_service(database_url, trust_now):
    from app.modules.knowledge_sources.service import SourceService

    settings = TrustSettings(database_url, "j" * 48, "a" * 48)
    database = create_pool(database_url)
    database.open(wait=True)
    try:
        yield SourceService(settings, database, FrozenClock(trust_now))
    finally:
        database.close()


def source_rows(database_url, source_id):
    with connect(database_url) as conn:
        record = conn.execute(
            "SELECT * FROM trust.source_records WHERE id=%s", (UUID(source_id),)
        ).fetchone()
        provisions = conn.execute(
            "SELECT * FROM trust.source_provisions WHERE source_id=%s ORDER BY provision_key",
            (UUID(source_id),),
        ).fetchall()
        return record, provisions


def test_staging_preserves_original_bytes_and_exact_provenance(
    source_service, database_url, trust_actor, trust_now
):
    prepared = prepared_source(trust_now)
    result = source_service.stage(trust_actor(), prepared)
    assert result["draft"] == 1
    assert result["quarantined"] == 0
    assert result["replayed"] is False
    record, provisions = source_rows(database_url, result["source_id"])
    assert record["artifact_sha256"] == prepared.spec.sha256
    assert record["extraction_sha256"] == prepared.extraction_sha256
    assert record["source_url"] == prepared.spec.source_url
    assert record["final_url"] == prepared.final_url
    assert record["principal"] == "cf"
    assert record["product"] == "spaylater"
    assert record["extracted_pages"] == [
        {"number": page.number, "text": page.text} for page in prepared.pages
    ]
    assert provisions[0]["page_start"] == provisions[0]["page_end"] == 1
    assert provisions[0]["stage_status"] == "draft"
    with connect(database_url) as conn:
        artifact = conn.execute(
            "SELECT * FROM trust.source_artifacts WHERE sha256=%s", (prepared.spec.sha256,)
        ).fetchone()
        assert bytes(artifact["payload"]) == prepared.body
        assert artifact["page_count"] == 1
        document = conn.execute(
            "SELECT * FROM trust.knowledge_documents WHERE id=%s",
            (provisions[0]["knowledge_document_id"],),
        ).fetchone()
        assert document["status"] == "draft"
        assert document["review_status"] == "review_required"
        assert "Contact only the verified borrower." in document["text"]
        assert "Separate provision." not in document["text"]


def test_staging_is_idempotent_and_drafts_never_retrieve(
    source_service, database_url, trust_actor, trust_now
):
    prepared = prepared_source(trust_now)
    first = source_service.stage(trust_actor(), prepared)
    second = source_service.stage(trust_actor(), prepared)
    assert second["source_id"] == first["source_id"]
    assert second["replayed"] is True
    with connect(database_url) as conn:
        for table in (
            "source_artifacts",
            "source_records",
            "source_provisions",
            "knowledge_documents",
        ):
            assert conn.execute(f"SELECT count(*) AS n FROM trust.{table}").fetchone()["n"] == 1
        assert (
            knowledge.retrieve(conn, trust_actor("personal"), "verified borrower", trust_now)[
                "results"
            ]
            == []
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM trust.knowledge_documents WHERE status='approved'"
            ).fetchone()["n"]
            == 0
        )


def test_new_extraction_manifest_appends_record_and_reuses_original_pdf(
    source_service, database_url, trust_actor, trust_now
):
    original = prepared_source(trust_now)
    first = source_service.stage(trust_actor(), original)
    corrected_pages = tuple(
        replace(page, text=page.text.replace("verified borrower", "verified borrower only"))
        for page in original.pages
    )
    corrected_hash = pages_hash(corrected_pages)
    revised = replace(
        original,
        spec=original.spec.model_copy(update={"extraction_sha256": corrected_hash}),
        pages=corrected_pages,
        extraction_sha256=corrected_hash,
        extraction_method="curator_text_revision",
        extraction_review_required=True,
    )
    second = source_service.stage(trust_actor(), revised)
    assert second["source_id"] != first["source_id"]
    assert second["replayed"] is False
    assert source_service.stage(trust_actor(), revised)["replayed"] is True
    with connect(database_url) as conn:
        artifacts = conn.execute("SELECT * FROM trust.source_artifacts").fetchall()
        records = conn.execute(
            "SELECT extraction_sha256,extraction_review_required FROM trust.source_records"
        ).fetchall()
        assert len(artifacts) == 1
        assert bytes(artifacts[0]["payload"]) == original.body
        assert len(records) == 2
        assert {row["extraction_sha256"] for row in records} == {
            original.extraction_sha256,
            corrected_hash,
        }
        assert sum(row["extraction_review_required"] for row in records) == 1
        assert (
            conn.execute("SELECT count(*) AS n FROM trust.source_provisions").fetchone()["n"] == 2
        )
        assert (
            knowledge.retrieve(conn, trust_actor("personal"), "borrower", trust_now)["results"]
            == []
        )


@pytest.mark.parametrize("table", ["source_artifacts", "source_records", "source_provisions"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "TRUNCATE"])
def test_archived_records_block_mutation(
    source_service, database_url, trust_actor, trust_now, table, operation
):
    source_service.stage(trust_actor(), prepared_source(trust_now))
    statement = {
        "UPDATE": f"UPDATE trust.{table} SET "
        + ("page_count=page_count" if table == "source_artifacts" else "id=id"),
        "DELETE": f"DELETE FROM trust.{table}",
        "TRUNCATE": f"TRUNCATE trust.{table} CASCADE",
    }[operation]
    with pytest.raises(psycopg.Error), connect(database_url) as conn:
        conn.execute(statement)
    with connect(database_url) as conn:
        assert conn.execute(f"SELECT count(*) AS n FROM trust.{table}").fetchone()["n"] == 1


def test_extraction_review_cannot_be_promoted_by_qa(
    source_service, database_url, trust_actor, trust_now
):
    result = source_service.stage(trust_actor(), prepared_source(trust_now, review_required=True))
    record, provisions = source_rows(database_url, result["source_id"])
    assert record["extraction_review_required"] is True
    document_id = str(provisions[0]["knowledge_document_id"])
    with pytest.raises(TrustError, match="SOURCE_REVIEW_REQUIRED"), connect(database_url) as conn:
        knowledge.qa_review(conn, trust_actor("qa"), document_id, trust_now)
    with connect(database_url) as conn:
        row = conn.execute(
            "SELECT status,qa_by FROM trust.knowledge_documents WHERE id=%s", (document_id,)
        ).fetchone()
        assert row == {"status": "draft", "qa_by": None}


def test_pii_quarantine_keeps_pdf_without_reusable_knowledge(
    source_service, database_url, trust_actor, trust_now
):
    prepared = prepared_source(
        trust_now,
        text="Pasal 62\nSynthetic contact: synthetic@example.test.\nPasal 63\nOther provision.",
    )
    result = source_service.stage(trust_actor(), prepared)
    assert result["quarantined"] == 1
    assert result["draft"] == 0
    _, provisions = source_rows(database_url, result["source_id"])
    assert provisions[0]["stage_status"] == "quarantined"
    assert provisions[0]["knowledge_document_id"] is None
    assert provisions[0]["reason_code"]
    with connect(database_url) as conn:
        assert (
            bytes(
                conn.execute(
                    "SELECT payload FROM trust.source_artifacts WHERE sha256=%s",
                    (prepared.spec.sha256,),
                ).fetchone()["payload"]
            )
            == prepared.body
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM trust.knowledge_documents").fetchone()["n"] == 0
        )


@pytest.mark.parametrize("legal_status", ["future", "repealed"])
def test_non_current_provisions_remain_quarantined(
    source_service, database_url, trust_actor, trust_now, legal_status
):
    result = source_service.stage(
        trust_actor(), prepared_source(trust_now, legal_status=legal_status)
    )
    assert result["quarantined"] == 1
    assert result["draft"] == 0
    with connect(database_url) as conn:
        assert (
            knowledge.retrieve(conn, trust_actor("personal"), "verified borrower", trust_now)[
                "results"
            ]
            == []
        )


@pytest.mark.parametrize("parent_status", ["future", "repealed"])
def test_non_current_parent_cannot_be_overridden_by_in_force_child(
    source_service, database_url, trust_actor, trust_now, parent_status
):
    prepared = prepared_source(trust_now)
    contradictory = replace(
        prepared, spec=prepared.spec.model_copy(update={"legal_status": parent_status})
    )
    assert contradictory.spec.provisions[0].legal_status == "in_force"
    result = source_service.stage(trust_actor(), contradictory)
    assert result["quarantined"] == 1
    assert result["draft"] == 0
    record, provisions = source_rows(database_url, result["source_id"])
    assert record["legal_status"] == parent_status
    assert provisions[0]["knowledge_document_id"] is None
    with connect(database_url) as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM trust.knowledge_documents").fetchone()["n"] == 0
        )


def test_hash_tampering_refused_before_any_archive_write(
    source_service, database_url, trust_actor, trust_now
):
    prepared = prepared_source(trust_now)
    for modified in (
        replace(prepared, body=prepared.body + b"\n% altered source"),
        replace(prepared, extraction_sha256="0" * 64),
        replace(prepared, pages=(replace(prepared.pages[0], text="Altered extracted text."),)),
    ):
        with pytest.raises(TrustError):
            source_service.stage(trust_actor(), modified)
    with connect(database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM trust.source_records").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM trust.source_artifacts").fetchone()["n"] == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://www.ojk.go.id/test.pdf",
        "https://unapproved.example/test.pdf",
        "https://@ojk.go.id/test.pdf",
        "https://www.ojk.go.id:444/test.pdf",
    ],
)
def test_forbidden_source_or_redirect_refused(source_service, trust_actor, trust_now, url):
    prepared = prepared_source(trust_now)
    with pytest.raises(TrustError):
        source_service.stage(
            trust_actor(),
            replace(prepared, spec=prepared.spec.model_copy(update={"source_url": url})),
        )
    with pytest.raises(TrustError):
        source_service.stage(trust_actor(), replace(prepared, final_url=url))


@pytest.mark.parametrize(
    "role", ["personal", "process", "manager", "operator", "officer", "qa", "compliance"]
)
def test_non_ingestion_roles_cannot_stage(source_service, trust_actor, trust_now, role):
    with pytest.raises(TrustError, match="CAPABILITY_DENIED"):
        source_service.stage(trust_actor(role), prepared_source(trust_now))


def test_cross_scope_stage_read_and_download_denied(source_service, trust_actor, trust_now):
    prepared = prepared_source(trust_now)
    with pytest.raises(TrustError):
        source_service.stage(trust_actor("knowledge", "ldn", "spinjam"), prepared)
    result = source_service.stage(trust_actor(), prepared)
    other = trust_actor("knowledge", "ldn", "spinjam")
    assert source_service.list_sources(other, 25, 0)["total"] == 0
    for read in (source_service.detail, source_service.download):
        with pytest.raises(TrustError):
            read(other, UUID(result["source_id"]))
    for role in ("personal", "process", "manager", "operator", "officer"):
        with pytest.raises(TrustError, match="CAPABILITY_DENIED"):
            source_service.download(trust_actor(role), UUID(result["source_id"]))
    body, digest = source_service.download(trust_actor("compliance"), UUID(result["source_id"]))
    assert body == prepared.body
    assert digest == prepared.spec.sha256


def test_upgrade_from_existing_trust_preserves_knowledge_and_audit(
    empty_database_url, trust_actor, trust_now
):
    config = migration_config(empty_database_url)
    command.upgrade(config, "0002_trust")
    with connect(empty_database_url) as conn:
        document = approved_document(conn, trust_actor, trust_now)
        before_document = conn.execute(
            "SELECT * FROM trust.knowledge_documents WHERE id=%s", (document["id"],)
        ).fetchone()
        before_audit = conn.execute("SELECT * FROM trust.audit_events ORDER BY seq").fetchall()
        before_chunks = conn.execute("SELECT * FROM trust.knowledge_chunks ORDER BY id").fetchall()
    command.upgrade(config, "head")
    with connect(empty_database_url) as conn:
        assert (
            conn.execute(
                "SELECT * FROM trust.knowledge_documents WHERE id=%s", (document["id"],)
            ).fetchone()
            == before_document
        )
        assert (
            conn.execute("SELECT * FROM trust.audit_events ORDER BY seq").fetchall() == before_audit
        )
        assert (
            conn.execute("SELECT * FROM trust.knowledge_chunks ORDER BY id").fetchall()
            == before_chunks
        )
        assert conn.execute("SELECT count(*) AS n FROM trust.source_records").fetchone()["n"] == 0


def test_empty_source_archive_downgrade_and_reupgrade_preserve_existing_trust(
    database_url, trust_actor, trust_now
):
    with connect(database_url) as conn:
        document = approved_document(conn, trust_actor, trust_now)
    config = migration_config(database_url)
    command.downgrade(config, "0002_trust")
    with connect(database_url) as conn:
        assert (
            conn.execute("SELECT to_regclass('trust.source_records') AS table_name").fetchone()[
                "table_name"
            ]
            is None
        )
        assert (
            conn.execute(
                "SELECT status FROM trust.knowledge_documents WHERE id=%s", (document["id"],)
            ).fetchone()["status"]
            == "approved"
        )
    command.upgrade(config, "head")
    with connect(database_url) as conn:
        assert conn.execute("SELECT count(*) AS n FROM trust.source_records").fetchone()["n"] == 0
        assert (
            conn.execute(
                "SELECT status FROM trust.knowledge_documents WHERE id=%s", (document["id"],)
            ).fetchone()["status"]
            == "approved"
        )


def test_populated_archive_downgrade_refuses_without_data_loss(
    source_service, database_url, trust_actor, trust_now
):
    prepared = prepared_source(trust_now)
    result = source_service.stage(trust_actor(), prepared)
    with pytest.raises(RuntimeError, match="Cannot downgrade populated source archives"):
        command.downgrade(migration_config(database_url), "0002_trust")
    record, provisions = source_rows(database_url, result["source_id"])
    assert record["artifact_sha256"] == prepared.spec.sha256
    assert len(provisions) == 1
    with connect(database_url) as conn:
        assert (
            conn.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"]
            == REQUIRED_REVISION
        )
        assert (
            bytes(
                conn.execute(
                    "SELECT payload FROM trust.source_artifacts WHERE sha256=%s",
                    (prepared.spec.sha256,),
                ).fetchone()["payload"]
            )
            == prepared.body
        )
