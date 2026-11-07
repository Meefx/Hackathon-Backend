"""Join the immutable source archive and canonical collection migration histories.

Both branches retain their already published revisions. This merge changes no data.
"""

revision = "0006_source_collection_merge"
down_revision = ("0003_source_archive", "0005_knowledge_collection")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
