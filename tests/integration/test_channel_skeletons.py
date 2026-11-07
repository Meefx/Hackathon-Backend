import pytest

from app.integrations.channels.contracts import AuthorizedIntent
from app.integrations.channels.gateway import ChannelGateway
from app.shared.errors import DomainError


@pytest.mark.parametrize("channel", ["whatsapp", "email", "phone", "unsupported"])
def test_unavailable_provider_never_fakes_delivery(channel):
    with pytest.raises(DomainError) as error:
        ChannelGateway().resolve(channel).dispatch(
            AuthorizedIntent("key", "message", "account", "synthetic", "test")
        )
    assert error.value.code == "channel_unavailable"


def test_simulator_same_intent_has_deterministic_receipt():
    adapter = ChannelGateway().resolve("simulated_chat")
    intent = AuthorizedIntent("key", "message", "account", "synthetic", "test")
    assert adapter.dispatch(intent) == adapter.dispatch(intent)
    assert adapter.dispatch(intent)["status"] == "simulated_sent"
