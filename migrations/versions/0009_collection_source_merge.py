"""Join the source archive and collection channel migration histories."""

revision = "0009_collection_source_merge"
down_revision = ("0006_source_collection_merge", "0008_whatsapp_collection")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
