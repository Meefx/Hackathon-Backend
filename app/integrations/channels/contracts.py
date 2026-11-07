from dataclasses import dataclass

from app.shared.errors import DomainError


@dataclass(frozen=True)
class AuthorizedIntent:
    intent_key: str
    message_id: str
    account_id: str
    recipient_id: str
    text: str


class UnavailableChannelAdapter:
    name = "unavailable"

    def capabilities(self):
        return []

    def dispatch(self, authorized_intent):
        raise DomainError("channel_unavailable", "Channel provider is unavailable", 503)

    def normalize_verified_event(self, event):
        raise DomainError("channel_unavailable", "Verified webhook is unavailable", 503)
