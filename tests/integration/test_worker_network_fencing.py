from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.config import get_settings
from app.db.connection import connect
from app.integrations.knowledge.gateway import DemoKnowledgeGateway
from app.modules.collection.service import CollectionService
from app.modules.knowledge_context.contracts import KnowledgeQuery
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run
from app.seed import seed
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.workers.collection import claim


def test_network_return_cannot_persist_after_worker_lease_expires(database_url, monkeypatch):
    seed()
    now = datetime(2026, 11, 7, 3, tzinfo=UTC)
    settings = get_settings()
    run = start_run(
        settings,
        "CF-1001",
        StartRunRequest(instruction="Reminder", idempotency_key=uuid4()),
        Caller(),
        now,
    )
    _, attempt, fence = claim(settings, now, "worker")
    caller = Caller(
        principal_id="local-worker",
        actor_kind="worker",
        account_id="CF-1001",
        run_id=run["run_id"],
        execution_attempt_id=attempt,
        fence=fence,
    )
    current = [now]
    original = DemoKnowledgeGateway.query_approved

    def slow_query(self, request, caller):
        result = original(self, request, caller)
        current[0] += timedelta(seconds=120)
        return result

    monkeypatch.setattr(DemoKnowledgeGateway, "query_approved", slow_query)
    service = CollectionService(settings, clock=lambda: current[0])
    with pytest.raises(DomainError, match="Active execution"):
        service.retrieve_approved_context(
            run["run_id"],
            KnowledgeQuery(
                query="SOP", purpose="collection_draft", deadline_at=now + timedelta(seconds=30)
            ),
            caller,
        )
    with connect(database_url) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM knowledge_snapshots").fetchone()["n"] == 0
        )
