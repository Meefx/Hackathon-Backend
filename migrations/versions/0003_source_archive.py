"""Immutable original public sources and scoped provision provenance.

Revision ID: 0003_source_archive
Revises: 0002_trust

No source is approved by this migration. Populated archives refuse downgrade.
"""

from alembic import op

revision = "0003_source_archive"
down_revision = "0002_trust"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE trust.source_artifacts (
            sha256 text PRIMARY KEY CHECK (sha256 ~ '^[a-f0-9]{64}$'),
            payload bytea NOT NULL CHECK (octet_length(payload) BETWEEN 8 AND 20971520),
            content_type text NOT NULL CHECK (content_type='application/pdf'),
            page_count integer NOT NULL CHECK (page_count BETWEEN 1 AND 500),
            stored_at timestamptz NOT NULL
        );
        CREATE TABLE trust.source_records (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            principal text NOT NULL,
            product text NOT NULL,
            source_key text NOT NULL,
            title text NOT NULL,
            publisher text NOT NULL,
            source_url text NOT NULL,
            final_url text NOT NULL,
            source_version text NOT NULL,
            source_class text NOT NULL CHECK (source_class IN ('primary','guidance')),
            artifact_sha256 text NOT NULL REFERENCES trust.source_artifacts(sha256),
            extracted_pages jsonb NOT NULL CHECK (jsonb_typeof(extracted_pages)='array'),
            extraction_sha256 text NOT NULL CHECK (extraction_sha256 ~ '^[a-f0-9]{64}$'),
            extraction_method text NOT NULL,
            extraction_review_required boolean NOT NULL,
            manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[a-f0-9]{64}$'),
            legal_status text NOT NULL CHECK (legal_status IN
                ('in_force','partially_repealed','future','repealed','draft','review_required')),
            effective_from timestamptz,
            effective_until timestamptz,
            metadata jsonb NOT NULL,
            created_by text NOT NULL,
            created_at timestamptz NOT NULL,
            UNIQUE (id,principal,product),
            UNIQUE (principal,product,source_key,artifact_sha256,manifest_sha256),
            CHECK (effective_until IS NULL OR effective_from IS NULL OR effective_until>effective_from)
        );
        CREATE INDEX source_records_scope ON trust.source_records(principal,product,source_key);
        CREATE TABLE trust.source_provisions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id uuid NOT NULL,
            principal text NOT NULL,
            product text NOT NULL,
            provision_key text NOT NULL,
            locator text NOT NULL,
            page_start integer NOT NULL CHECK (page_start>0),
            page_end integer NOT NULL CHECK (page_end>=page_start),
            knowledge_document_id uuid REFERENCES trust.knowledge_documents(id),
            stage_status text NOT NULL CHECK (stage_status IN ('draft','quarantined')),
            reason_code text,
            text_sha256 text NOT NULL CHECK (text_sha256 ~ '^[a-f0-9]{64}$'),
            metadata jsonb NOT NULL,
            UNIQUE (source_id,provision_key),
            FOREIGN KEY (source_id,principal,product)
                REFERENCES trust.source_records(id,principal,product)
        );
        CREATE TRIGGER source_artifacts_immutable BEFORE UPDATE OR DELETE ON trust.source_artifacts
            FOR EACH ROW EXECUTE FUNCTION trust.block_audit_mutation();
        CREATE TRIGGER source_records_immutable BEFORE UPDATE OR DELETE ON trust.source_records
            FOR EACH ROW EXECUTE FUNCTION trust.block_audit_mutation();
        CREATE TRIGGER source_provisions_immutable BEFORE UPDATE OR DELETE ON trust.source_provisions
            FOR EACH ROW EXECUTE FUNCTION trust.block_audit_mutation();
        CREATE TRIGGER source_artifacts_no_truncate BEFORE TRUNCATE ON trust.source_artifacts
            FOR EACH STATEMENT EXECUTE FUNCTION trust.block_audit_mutation();
        CREATE TRIGGER source_records_no_truncate BEFORE TRUNCATE ON trust.source_records
            FOR EACH STATEMENT EXECUTE FUNCTION trust.block_audit_mutation();
        CREATE TRIGGER source_provisions_no_truncate BEFORE TRUNCATE ON trust.source_provisions
            FOR EACH STATEMENT EXECUTE FUNCTION trust.block_audit_mutation();
    """)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "LOCK TABLE trust.source_artifacts,trust.source_records,trust.source_provisions "
        "IN ACCESS EXCLUSIVE MODE"
    )
    for name in ("source_artifacts", "source_records", "source_provisions"):
        if connection.exec_driver_sql(f"SELECT EXISTS (SELECT 1 FROM trust.{name})").scalar():
            raise RuntimeError(
                "Cannot downgrade populated source archives; use a forward migration"
            )
    op.execute("DROP TABLE trust.source_provisions,trust.source_records,trust.source_artifacts")
