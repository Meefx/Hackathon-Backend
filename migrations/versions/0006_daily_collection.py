"""Durable daily admission provenance and delivery reconciliation."""

from alembic import op

revision = "0006_daily_collection"
down_revision = "0005_knowledge_collection"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE collection_schedule_items (
        id TEXT PRIMARY KEY,
        business_date DATE NOT NULL,
        account_id TEXT NOT NULL REFERENCES accounts(id),
        run_id TEXT UNIQUE REFERENCES collection_runs(id),
        facts_hash TEXT,
        state TEXT NOT NULL CHECK(state IN
          ('queued','running','sent','deferred','needs_attention','resolved')),
        reason_codes JSONB NOT NULL DEFAULT '[]',
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE(business_date, account_id));
      CREATE INDEX collection_schedule_pending ON collection_schedule_items(state, business_date);
      CREATE TABLE collection_schedule_state (
        id INT PRIMARY KEY CHECK(id=1),
        last_scan_at TIMESTAMPTZ NOT NULL);
    """)


def downgrade():
    op.execute("DROP TABLE collection_schedule_state; DROP TABLE collection_schedule_items")
