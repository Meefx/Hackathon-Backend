"""Bind approved knowledge and borrower authority to canonical collection.

Existing accounts remain unprovisioned. Synthetic authority is an explicit seed,
and historical snapshots retain their original version-one hash.
"""

from alembic import op

revision = "0005_knowledge_collection"
down_revision = "0004_collection_trust_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE collection_authorities (
        account_id TEXT PRIMARY KEY REFERENCES accounts(id),
        principal TEXT NOT NULL, product TEXT NOT NULL, borrower_ref UUID NOT NULL,
        borrower_timezone TEXT NOT NULL,
        borrower_owned BOOLEAN NOT NULL DEFAULT FALSE,
        verified BOOLEAN NOT NULL DEFAULT FALSE, opted_out BOOLEAN NOT NULL DEFAULT TRUE,
        is_synthetic BOOLEAN NOT NULL DEFAULT FALSE,
        data_verified_at TIMESTAMPTZ NOT NULL,
        mandate_active BOOLEAN NOT NULL DEFAULT FALSE,
        calendar_years JSONB NOT NULL DEFAULT '[]', holidays JSONB NOT NULL DEFAULT '[]');
      CREATE INDEX authority_borrower_scope
        ON collection_authorities(principal,product,borrower_ref);
      ALTER TABLE knowledge_snapshots
        ADD COLUMN critical_context JSONB NOT NULL DEFAULT '[]',
        ADD COLUMN verification_profile JSONB,
        ADD COLUMN snapshot_hash_version INTEGER NOT NULL DEFAULT 1
          CHECK(snapshot_hash_version IN (1,2));
      CREATE FUNCTION reject_knowledge_snapshot_change() RETURNS trigger
      LANGUAGE plpgsql AS $$
      BEGIN
        RAISE EXCEPTION 'knowledge snapshots are immutable';
      END $$;
      CREATE TRIGGER knowledge_snapshots_immutable
        BEFORE UPDATE OR DELETE ON knowledge_snapshots
        FOR EACH ROW EXECUTE FUNCTION reject_knowledge_snapshot_change();
      CREATE TRIGGER knowledge_snapshots_no_truncate
        BEFORE TRUNCATE ON knowledge_snapshots
        FOR EACH STATEMENT EXECUTE FUNCTION reject_knowledge_snapshot_change();
      CREATE TABLE collection_message_bindings (
        message_id TEXT PRIMARY KEY REFERENCES collection_messages(id),
        template_binding JSONB, snapshot_hash TEXT NOT NULL,
        evidence_references JSONB NOT NULL);
      CREATE TABLE legacy_case_account_mappings (
        case_id UUID PRIMARY KEY REFERENCES trust.cases(id),
        account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id));
    """)


def downgrade() -> None:
    connection = op.get_bind()
    tables = (
        "collection_authorities",
        "collection_message_bindings",
        "legacy_case_account_mappings",
    )
    for table in tables:
        connection.exec_driver_sql(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE")
    if any(
        connection.exec_driver_sql(f"SELECT EXISTS(SELECT 1 FROM {table})").scalar()
        for table in tables
    ):
        raise RuntimeError("Cannot downgrade populated knowledge integration data")
    if connection.exec_driver_sql(
        "SELECT EXISTS(SELECT 1 FROM knowledge_snapshots WHERE snapshot_hash_version=2)"
    ).scalar():
        raise RuntimeError("Cannot downgrade version-two knowledge snapshots")
    op.execute("""
      DROP TABLE legacy_case_account_mappings;
      DROP TABLE collection_message_bindings;
      DROP TRIGGER knowledge_snapshots_immutable ON knowledge_snapshots;
      DROP TRIGGER knowledge_snapshots_no_truncate ON knowledge_snapshots;
      DROP FUNCTION reject_knowledge_snapshot_change();
      ALTER TABLE knowledge_snapshots DROP COLUMN critical_context,
        DROP COLUMN verification_profile, DROP COLUMN snapshot_hash_version;
      DROP TABLE collection_authorities;
    """)
