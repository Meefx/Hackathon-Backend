from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.config import get_settings
from app.db.connection import check_ready, connect
from app.main import create_app
from app.modules.knowledge import service as knowledge
from app.modules.knowledge.authorization import Actor
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run
from app.seed import seed
from app.shared.caller import Caller
from app.shared.idempotency import digest
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
    now = datetime(2026, 11, 7, 3, tzinfo=UTC)
    legacy = None
    if prior_head == "0002_trust":
        with connect(empty_database_url) as connection:
            document = knowledge.ingest(
                connection,
                Actor("author", "knowledge", "cf", "spaylater"),
                {
                    "title": "Synthetic legacy SOP",
                    "text": "Offer a human officer for assistance.",
                    "source_uri": "synthetic://migration/sop",
                    "source_class": "synthetic",
                    "source_version": "legacy-v1",
                    "source_locator": "SOP",
                    "effective_from": "2026-01-01T00:00:00Z",
                    "access_roles": ["personal"],
                },
                now,
            )
            knowledge.qa_review(
                connection, Actor("qa", "qa", "cf", "spaylater"), document["id"], now
            )
            knowledge.approve(
                connection,
                Actor("human", "compliance", "cf", "spaylater"),
                document["id"],
                "Independent synthetic migration approval",
                now,
            )
            preserved = {
                table: connection.execute(f"SELECT * FROM trust.{table} ORDER BY 1").fetchall()
                for table in ("knowledge_documents", "knowledge_chunks", "audit_events")
            }
    else:
        accepted = start_run(
            get_settings(),
            "CF-PRESERVE",
            StartRunRequest(
                instruction="Preserve legacy evidence",
                runtime_mode="simulated",
                idempotency_key=uuid4(),
            ),
            Caller(),
            now,
        )
        legacy = {
            "document_id": "legacy-sop",
            "document_version": "1",
            "snippet_id": "legacy-snippet",
            "citation_id": "legacy-citation",
            "title": "Legacy SOP",
            "excerpt": "Sopan dan bantu.",
            "content_hash": digest("Sopan dan bantu."),
            "approved_at": now.isoformat().replace("+00:00", "Z"),
            "effective_from": None,
            "effective_until": None,
            "source_uri": None,
        }
        with connect(empty_database_url) as connection:
            connection.execute(
                "INSERT INTO knowledge_snapshots(id,run_id,status,source_mode,required,retrieved_at,"
                "snapshot_hash,sources,reason_code) VALUES (%s,%s,'ready','demo_fixture',TRUE,%s,%s,%s,NULL)",
                (str(uuid4()), accepted["run_id"], now, digest([legacy]), Jsonb([legacy])),
            )
            preserved = {
                table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("collection_runs", "knowledge_snapshots", "audit_events")
            }
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
        for table, rows in preserved.items():
            prefix = "trust." if prior_head == "0002_trust" else ""
            columns = ",".join(rows[0])
            assert (
                connection.execute(f"SELECT {columns} FROM {prefix}{table} ORDER BY 1").fetchall()
                == rows
            )
    assert check_ready(empty_database_url)
    if legacy:
        with TestClient(create_app()) as client:
            response = client.get(f"/api/v1/runs/{accepted['run_id']}/knowledge")
            assert response.status_code == 200
            snapshot = response.json()["items"][0]
            assert snapshot["snapshot_hash_version"] == 1
            assert snapshot["sources"] == [legacy]
            assert snapshot["snapshot_hash"] == digest(snapshot["sources"])


def test_combined_migrations_have_one_integration_head():
    assert ScriptDirectory.from_config(migration_config()).get_heads() == [
        "0005_knowledge_collection"
    ]


def test_populated_integration_downgrade_preserves_authority(database_url):
    seed()
    with connect(database_url) as connection:
        before = connection.execute(
            "SELECT * FROM collection_authorities ORDER BY account_id"
        ).fetchall()
    with pytest.raises(RuntimeError, match="Cannot downgrade populated knowledge integration"):
        command.downgrade(migration_config(database_url), "0004_collection_trust_merge")
    with connect(database_url) as connection:
        assert (
            connection.execute(
                "SELECT * FROM collection_authorities ORDER BY account_id"
            ).fetchall()
            == before
        )
    assert check_ready(database_url)
