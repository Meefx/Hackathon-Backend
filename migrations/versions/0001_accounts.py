"""Initial PostgreSQL portfolio schema, with whole-IDR balances and calendar dates.

Revision ID: 0001_accounts
Revises: none
Downgrade drops the accounts table and its data. Restore a verified backup to recover.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_accounts"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("borrower_name", sa.Text(), nullable=False),
        sa.Column("loan_reference", sa.Text(), nullable=False),
        sa.Column("outstanding_amount", sa.BigInteger(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("assigned_to", sa.Text(), nullable=False),
        sa.UniqueConstraint("loan_reference", name="uq_accounts_loan_reference"),
        sa.CheckConstraint("outstanding_amount >= 0", name="ck_accounts_nonnegative_amount"),
    )
    op.create_index("ix_accounts_due_date_id", "accounts", ["due_date", "id"])


def downgrade() -> None:
    op.drop_table("accounts")
