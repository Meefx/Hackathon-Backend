from app.config import Settings
from app.integrations.channels.contracts import AuthorizedIntent, UnavailableChannelAdapter
from app.integrations.gmail.client import GmailClient, sandbox_configured
from app.shared.errors import DomainError


class EmailAdapter(UnavailableChannelAdapter):
    name = "email"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings
        self._client: GmailClient | None = None

    def _require_client(self) -> GmailClient:
        if (
            not self.settings
            or not self.settings.demo_fixture_enabled
            or not sandbox_configured(self.settings)
        ):
            raise DomainError("channel_unavailable", "Channel provider is unavailable", 503)
        if self._client is None:
            self._client = GmailClient(self.settings)
        return self._client

    def prepare(self) -> None:
        self._require_client().prepare_collection()

    def dispatch(self, authorized_intent: AuthorizedIntent) -> dict[str, str]:
        return self._require_client().send_collection_message(authorized_intent)
