"""Durable Gmail collection demos and pre-send reservations."""

from alembic import op

revision = "0010_gmail_collection_demo"
down_revision = "0009_collection_source_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE collection_email_demos (
            id TEXT PRIMARY KEY,
            request_key TEXT NOT NULL UNIQUE,
            caller_id TEXT NOT NULL,
            account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id),
            run_id TEXT NOT NULL UNIQUE REFERENCES collection_runs(id),
            sender_email TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN
                ('queued','running','submitted','blocked','failed','delivery_unknown')),
            reason_codes JSONB NOT NULL DEFAULT '[]',
            message_id TEXT UNIQUE REFERENCES collection_messages(id),
            provider_message_id TEXT UNIQUE,
            reserved_at TIMESTAMPTZ,
            delivery_deadline_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        );
        CREATE INDEX collection_email_demos_pending_idx
            ON collection_email_demos(created_at,id)
            WHERE status IN ('queued','running');
    """)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM collection_email_demos)").scalar():
        raise RuntimeError("Cannot downgrade populated Gmail collection demo data")
    op.drop_table("collection_email_demos")
