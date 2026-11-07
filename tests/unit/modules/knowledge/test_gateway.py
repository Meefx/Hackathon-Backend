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
