"""Attributable case holds and monotonic observable run versions."""

from alembic import op

revision = "0003_review_holds"
down_revision = "0002_collection_state"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE collection_holds (
        id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
        review_id TEXT UNIQUE REFERENCES collection_reviews(id),
        kind TEXT NOT NULL CHECK(kind IN ('review','operator','legacy')),
        owner_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, released_at TIMESTAMPTZ);
      CREATE INDEX active_case_holds ON collection_holds(account_id) WHERE released_at IS NULL;
      INSERT INTO collection_holds(id,account_id,review_id,kind,owner_id,created_at)
        SELECT 'review:'||id,account_id,id,'review','local-operator',created_at
        FROM collection_reviews WHERE status='open';
      INSERT INTO collection_holds(id,account_id,kind,owner_id,created_at)
        SELECT 'legacy:'||account_id,account_id,'legacy','local-operator',updated_at
        FROM collection_cases WHERE operator_hold;
      ALTER TABLE knowledge_snapshots ADD COLUMN created_sequence BIGINT GENERATED ALWAYS AS IDENTITY;
      CREATE FUNCTION advance_run_version() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        NEW.version := GREATEST(NEW.version, OLD.version + 1);
        RETURN NEW;
      END $$;
      CREATE TRIGGER run_version BEFORE UPDATE ON collection_runs
        FOR EACH ROW EXECUTE FUNCTION advance_run_version();
      CREATE FUNCTION advance_parent_run_version() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        UPDATE collection_runs SET version=version+1 WHERE id=NEW.run_id;
        RETURN NEW;
      END $$;
      CREATE TRIGGER step_version AFTER INSERT OR UPDATE ON run_steps
        FOR EACH ROW EXECUTE FUNCTION advance_parent_run_version();
      CREATE TRIGGER artifact_version AFTER INSERT OR UPDATE ON run_artifacts
        FOR EACH ROW EXECUTE FUNCTION advance_parent_run_version();
      CREATE TRIGGER knowledge_version AFTER INSERT ON knowledge_snapshots
        FOR EACH ROW EXECUTE FUNCTION advance_parent_run_version();
    """)


def downgrade():
    op.execute("""
      UPDATE collection_cases c SET operator_hold=EXISTS(
        SELECT 1 FROM collection_holds h WHERE h.account_id=c.account_id
        AND h.released_at IS NULL AND h.kind IN ('operator','legacy'));
      DROP TRIGGER knowledge_version ON knowledge_snapshots;
      DROP TRIGGER artifact_version ON run_artifacts;
      DROP TRIGGER step_version ON run_steps;
      DROP FUNCTION advance_parent_run_version();
      DROP TRIGGER run_version ON collection_runs;
      DROP FUNCTION advance_run_version();
      ALTER TABLE knowledge_snapshots DROP COLUMN created_sequence;
      DROP TABLE collection_holds;
    """)
