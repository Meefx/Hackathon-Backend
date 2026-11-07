"""Persistent collection runs, deterministic gates and audit; preserves portfolio."""

from alembic import op

revision = "0002_collection_state"
down_revision = "0001_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE collection_policies (
      version TEXT PRIMARY KEY, active BOOLEAN NOT NULL DEFAULT FALSE,
      contact_start_hour INT NOT NULL CHECK(contact_start_hour BETWEEN 0 AND 23),
      contact_end_hour INT NOT NULL CHECK(contact_end_hour BETWEEN 1 AND 24),
      coverage_until DATE NOT NULL, maximum_daily_messages INT NOT NULL CHECK(maximum_daily_messages>0),
      mode TEXT NOT NULL, CHECK(contact_end_hour>contact_start_hour));
    CREATE UNIQUE INDEX one_active_policy ON collection_policies(active) WHERE active;
    CREATE TABLE collection_cases (
      account_id TEXT PRIMARY KEY REFERENCES accounts(id), version INT NOT NULL DEFAULT 1,
      recipient_id TEXT, recipient_verified BOOLEAN NOT NULL DEFAULT FALSE,
      eligible BOOLEAN NOT NULL DEFAULT FALSE, operator_hold BOOLEAN NOT NULL DEFAULT FALSE,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
    CREATE TABLE manager_sessions (
      id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), caller_id TEXT NOT NULL,
      title TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
      last_run_id TEXT, next_turn INT NOT NULL DEFAULT 1);
    CREATE TABLE collection_runs (
      id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
      manager_session_id TEXT NOT NULL REFERENCES manager_sessions(id), caller_id TEXT NOT NULL,
      request_key TEXT NOT NULL, payload JSONB NOT NULL, status TEXT NOT NULL
       CHECK(status IN ('queued','running','completed','blocked','failed','cancelled','interrupted')),
      outcome TEXT, requested_runtime_mode TEXT NOT NULL, actual_runtime_mode TEXT,
      phase TEXT NOT NULL DEFAULT 'queued', reason_codes JSONB NOT NULL DEFAULT '[]',
      version INT NOT NULL DEFAULT 1, session_turn_index INT NOT NULL,
      latest_event_seq INT NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL,
      started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, heartbeat_at TIMESTAMPTZ,
      cancellation_requested_at TIMESTAMPTZ, external_run_id TEXT, runtime_snapshot JSONB,
      UNIQUE(account_id,caller_id,request_key), UNIQUE(manager_session_id,session_turn_index));
    CREATE UNIQUE INDEX one_running_session ON collection_runs(manager_session_id) WHERE status='running';
    CREATE TABLE manager_turns (
      id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES manager_sessions(id),
      turn_index INT NOT NULL, run_id TEXT REFERENCES collection_runs(id), role TEXT NOT NULL,
      content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, UNIQUE(session_id,turn_index,role));
    CREATE TABLE collection_jobs (
      run_id TEXT PRIMARY KEY REFERENCES collection_runs(id), fence BIGINT NOT NULL DEFAULT 0,
      execution_attempt_id TEXT, lease_until TIMESTAMPTZ, worker_id TEXT, checkpoint TEXT NOT NULL DEFAULT 'queued');
    CREATE TABLE run_events (
      run_id TEXT NOT NULL REFERENCES collection_runs(id), sequence INT NOT NULL,
      type TEXT NOT NULL, safe_summary TEXT NOT NULL, evidence_origin TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(run_id,sequence));
    CREATE TABLE run_steps (
      id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES collection_runs(id), sequence INT NOT NULL,
      role TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL, evidence_origin TEXT NOT NULL,
      started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, UNIQUE(run_id,sequence));
    CREATE TABLE knowledge_snapshots (
      id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES collection_runs(id), status TEXT NOT NULL,
      source_mode TEXT NOT NULL, required BOOLEAN NOT NULL, retrieved_at TIMESTAMPTZ NOT NULL,
      snapshot_hash TEXT NOT NULL, sources JSONB NOT NULL, reason_code TEXT);
    CREATE TABLE collection_messages (
      id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
      run_id TEXT REFERENCES collection_runs(id), channel TEXT NOT NULL, direction TEXT NOT NULL,
      text TEXT NOT NULL, status TEXT NOT NULL, version INT NOT NULL DEFAULT 1,
      citation_ids JSONB NOT NULL DEFAULT '[]', created_at TIMESTAMPTZ NOT NULL);
    CREATE TABLE run_artifacts (
      id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES collection_runs(id), kind TEXT NOT NULL,
      text TEXT NOT NULL, message_id TEXT REFERENCES collection_messages(id),
      citation_ids JSONB NOT NULL DEFAULT '[]', review_state TEXT, created_at TIMESTAMPTZ NOT NULL);
    CREATE TABLE message_approvals (
      message_id TEXT PRIMARY KEY REFERENCES collection_messages(id), attestation TEXT NOT NULL,
      binding JSONB NOT NULL, expires_at TIMESTAMPTZ NOT NULL, guardrail JSONB NOT NULL);
    CREATE TABLE collection_reviews (
      id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
      run_id TEXT REFERENCES collection_runs(id), reason TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
      version INT NOT NULL DEFAULT 1, created_at TIMESTAMPTZ NOT NULL,
      resolved_at TIMESTAMPTZ, resolution_note TEXT);
    CREATE TABLE dispatch_receipts (
      id TEXT PRIMARY KEY, message_id TEXT NOT NULL UNIQUE REFERENCES collection_messages(id),
      account_id TEXT NOT NULL REFERENCES accounts(id), intent_key TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL);
    CREATE TABLE audit_events (
      id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
      run_id TEXT REFERENCES collection_runs(id), action TEXT NOT NULL, actor_kind TEXT NOT NULL,
      outcome TEXT NOT NULL, reason_codes JSONB NOT NULL, correlation_id TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL, previous_hash TEXT, event_hash TEXT NOT NULL,
      sequence BIGINT GENERATED ALWAYS AS IDENTITY);
    CREATE TABLE collection_requests (
      scope TEXT NOT NULL, key TEXT NOT NULL, payload_hash TEXT NOT NULL,
      response JSONB NOT NULL, PRIMARY KEY(scope,key));
    CREATE INDEX run_account_history ON collection_runs(account_id,created_at,id);
    CREATE INDEX message_account_history ON collection_messages(account_id,created_at,id);
    CREATE INDEX review_open ON collection_reviews(account_id,status);
    """)


def downgrade():
    for table in (
        "collection_requests",
        "audit_events",
        "dispatch_receipts",
        "collection_reviews",
        "message_approvals",
        "run_artifacts",
        "collection_messages",
        "knowledge_snapshots",
        "run_steps",
        "run_events",
        "collection_jobs",
        "manager_turns",
        "collection_runs",
        "manager_sessions",
        "collection_cases",
        "collection_policies",
    ):
        op.execute(f"DROP TABLE {table}")
