"""Versioned evidence, safety workflow and append-only audit storage.

Revision ID: 0002_trust
Revises: 0001_accounts

The SQL files next to this revision are immutable migration assets. An existing
unversioned trust schema is deliberately rejected rather than silently adopted.
Downgrade is allowed only while every trust table is empty. For a populated
database, use a reviewed forward migration or restore a verified backup.
"""

from pathlib import Path

from alembic import op

revision = "0002_trust"
down_revision = "0001_accounts"
branch_labels = None
depends_on = None

TABLES = (
    "audit_events",
    "knowledge_documents",
    "knowledge_chunks",
    "templates",
    "knowledge_dependencies",
    "cases",
    "contact_budgets",
    "attempts",
    "dispatches",
    "conversations",
    "request_records",
)


def upgrade() -> None:
    connection = op.get_bind()
    directory = Path(__file__).parent
    for filename in ("0002_trust_knowledge.sql", "0002_trust_runtime.sql"):
        connection.exec_driver_sql((directory / filename).read_text())


def downgrade() -> None:
    connection = op.get_bind()
    # Lock all tables before checking them, so a concurrent writer cannot race
    # the emptiness check. Identifiers come only from this revision's constants.
    names = ", ".join(f"trust.{name}" for name in TABLES)
    connection.exec_driver_sql(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE")
    if any(
        connection.exec_driver_sql(f"SELECT EXISTS (SELECT 1 FROM trust.{name})").scalar()
        for name in TABLES
    ):
        raise RuntimeError(
            "Cannot downgrade populated trust data; use a reviewed forward migration"
        )
    connection.exec_driver_sql("DROP SCHEMA trust CASCADE")
    # pgvector can be shared by other modules. Never remove the extension here.
