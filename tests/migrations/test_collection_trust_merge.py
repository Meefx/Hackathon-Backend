from datetime import date

import pytest
from alembic import command
from alembic.script import ScriptDirectory

from app.db.connection import check_ready, connect
from tests.conftest import migration_config


@pytest.mark.parametrize("prior_head", ["0002_trust", "0003_review_holds"])
def test_either_branch_upgrades_without_changing_portfolio(empty_database_url, prior_head):
    config = migration_config(empty_database_url)
    command.upgrade(config, prior_head)
    with connect(empty_database_url) as connection:
        connection.execute(
            "INSERT INTO accounts VALUES (%s,%s,%s,%s,%s,%s)",
            (
                "CF-PRESERVE",
                "Synthetic Borrower",
                "LN-PRESERVE",
                5_000_000_001,
                date(2026, 10, 1),
                "Operator",
            ),
        )
        before = connection.execute("SELECT * FROM accounts").fetchall()
    command.upgrade(config, "head")
    with connect(empty_database_url) as connection:
        assert connection.execute("SELECT * FROM accounts").fetchall() == before
        assert (
            connection.execute("SELECT count(*) AS n FROM collection_authorities").fetchone()["n"]
            == 0
        )
        connection.execute(
            "SELECT critical_context, verification_profile, snapshot_hash_version "
            "FROM knowledge_snapshots LIMIT 0"
        )
        connection.execute(
            "SELECT evidence_references, template_binding FROM collection_message_bindings LIMIT 0"
        )
    assert check_ready(empty_database_url)


def test_combined_migrations_have_one_integration_head():
    assert ScriptDirectory.from_config(migration_config()).get_heads() == [
        "0005_knowledge_collection"
    ]
