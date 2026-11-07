"""Persist WhatsApp test bindings, durable send reservations, and webhook receipts."""

from alembic import op

revision = "0008_whatsapp_collection"
down_revision = "0007_manager_portfolio"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE collection_whatsapp_connections (
        account_id TEXT PRIMARY KEY REFERENCES accounts(id),
        session TEXT NOT NULL, chat_id TEXT NOT NULL,
        previous_recipient_id TEXT, connected_at TIMESTAMPTZ NOT NULL,
        UNIQUE(session,chat_id));
      CREATE TABLE collection_whatsapp_deliveries (
        message_id TEXT PRIMARY KEY REFERENCES collection_messages(id),
        intent_key TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL,
        session TEXT NOT NULL, chat_id TEXT NOT NULL,
        status TEXT NOT NULL, provider_message_id TEXT UNIQUE,
        audit_event_id TEXT NOT NULL REFERENCES audit_events(id),
        response JSONB, created_at TIMESTAMPTZ NOT NULL);
      CREATE TABLE collection_whatsapp_events (
        event_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
        session TEXT NOT NULL, provider_message_id TEXT NOT NULL,
        received_at TIMESTAMPTZ NOT NULL);
    """)


def downgrade():
    connection = op.get_bind()
    for table in ("collection_whatsapp_connections", "collection_whatsapp_deliveries", "collection_whatsapp_events"):
        if connection.exec_driver_sql(f"SELECT EXISTS(SELECT 1 FROM {table})").scalar():
            raise RuntimeError("Cannot downgrade populated WhatsApp integration data")
    op.execute("DROP TABLE collection_whatsapp_events; DROP TABLE collection_whatsapp_deliveries; DROP TABLE collection_whatsapp_connections")
