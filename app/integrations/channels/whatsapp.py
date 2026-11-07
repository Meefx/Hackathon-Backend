from app.config import Settings
from app.integrations.channels.contracts import AuthorizedIntent, UnavailableChannelAdapter
from app.integrations.waha.client import WahaClient
from app.shared.errors import DomainError


class WhatsAppAdapter(UnavailableChannelAdapter):
    name = "whatsapp"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings
        self.client: WahaClient | None = None

    def prepare(self) -> WahaClient:
        if self.settings is None:
            raise DomainError("channel_unavailable", "Channel provider is unavailable", 503)
        self.client = WahaClient(self.settings)
        self.client.self_chat_id()
        return self.client

    def dispatch(self, authorized_intent: AuthorizedIntent) -> dict[str, str]:
        if not self.settings or not self.settings.waha_collection_enabled:
            return super().dispatch(authorized_intent)
        if not authorized_intent.recipient_id.startswith("whatsapp:"):
            raise DomainError("recipient_unverified", "WhatsApp recipient is not bound", 403)
        client = self.client or self.prepare()
        return client.send_text(
            authorized_intent.recipient_id.removeprefix("whatsapp:"), authorized_intent.text
        )
