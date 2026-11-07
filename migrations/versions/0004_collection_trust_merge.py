"""Join collection/review and evidence-control migration histories."""

revision = "0004_collection_trust_merge"
down_revision = ("0003_review_holds", "0002_trust")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
