"""Advisory manager conversations spanning the synthetic portfolio."""

from alembic import op

revision = "0007_manager_portfolio"
down_revision = "0006_daily_collection"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE manager_sessions ALTER COLUMN account_id DROP NOT NULL;
      ALTER TABLE manager_sessions ADD COLUMN context_account_id TEXT REFERENCES accounts(id);
      ALTER TABLE collection_runs ALTER COLUMN account_id DROP NOT NULL;
      ALTER TABLE run_artifacts ADD COLUMN proposal JSONB;
      CREATE UNIQUE INDEX portfolio_run_request
        ON collection_runs(caller_id,request_key) WHERE account_id IS NULL;
      CREATE INDEX manager_portfolio_sessions
        ON manager_sessions(caller_id,updated_at DESC,id) WHERE account_id IS NULL;
    """)


def downgrade():
    # A downgrade must never silently discard durable conversations or their evidence.
    connection = op.get_bind()
    if connection.exec_driver_sql(
        "SELECT EXISTS(SELECT 1 FROM manager_sessions WHERE account_id IS NULL) "
        "OR EXISTS(SELECT 1 FROM collection_runs WHERE account_id IS NULL)"
    ).scalar():
        raise RuntimeError("Portfolio conversations must be archived before this downgrade")
    op.execute("""
      DROP INDEX manager_portfolio_sessions;
      DROP INDEX portfolio_run_request;
      ALTER TABLE run_artifacts DROP COLUMN proposal;
      ALTER TABLE collection_runs ALTER COLUMN account_id SET NOT NULL;
      ALTER TABLE manager_sessions DROP COLUMN context_account_id;
      ALTER TABLE manager_sessions ALTER COLUMN account_id SET NOT NULL;
    """)
