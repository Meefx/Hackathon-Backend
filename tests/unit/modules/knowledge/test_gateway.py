from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.integrations.knowledge.gateway import DemoKnowledgeGateway, get_gateway
from app.modules.knowledge_context.contracts import KnowledgeQuery
from app.shared.caller import Caller


def test_fixture_guard_is_local_and_immutable():
    now = datetime(2026, 11, 9, tzinfo=UTC)
    request = KnowledgeQuery(
        query="polite contact", purpose="collection_draft", deadline_at=now + timedelta(seconds=10)
    )
    gateway = DemoKnowledgeGateway()
    source = gateway.query_approved(request, Caller()).sources[0]
    assert gateway.lock_references(None, [source], Caller()).validate(now).valid
    changed = source.model_copy(update={"snippet_id": "fabricated"})
    assert not gateway.lock_references(None, [changed], Caller()).validate(now).valid


def test_in_process_without_factory_does_not_fallback():
    settings = get_settings().model_copy(
        update={"knowledge_mode": "in_process", "knowledge_service_factory": None}
    )
    assert not isinstance(get_gateway(settings), DemoKnowledgeGateway)


def test_already_expired_query_never_connects(monkeypatch):
    import psycopg

    from app.integrations.knowledge.trust_adapter import InProcessKnowledgeGateway

    now = datetime(2026, 11, 9, tzinfo=UTC)

    def forbidden_connection(*args, **kwargs):
        raise AssertionError("Expired requests must not open a connection")

    monkeypatch.setattr(psycopg, "connect", forbidden_connection)
    gateway = InProcessKnowledgeGateway(clock=lambda: now)
    request = KnowledgeQuery(query="policy", purpose="collection_draft", deadline_at=now)
    assert gateway.query_approved(request, Caller()).reason_code == "knowledge_deadline_exceeded"


def test_connection_timeout_uses_remaining_budget_and_driver_floor(monkeypatch):
    import psycopg

    from app.integrations.knowledge.trust_adapter import InProcessKnowledgeGateway

    now = datetime(2026, 11, 9, tzinfo=UTC)
    observed = []

    def unavailable(*args, **kwargs):
        observed.append(kwargs["connect_timeout"])
        raise psycopg.OperationalError("Synthetic unavailable test database")

    monkeypatch.setattr(psycopg, "connect", unavailable)
    gateway = InProcessKnowledgeGateway(clock=lambda: now)
    for milliseconds in (200, 3200, 10000):
        request = KnowledgeQuery(
            query="policy",
            purpose="collection_draft",
            deadline_at=now + timedelta(milliseconds=milliseconds),
        )
        assert gateway.query_approved(request, Caller()).status == "error"
    assert observed == [2, 3, 5]
