from app.integrations.channels.email import EmailAdapter
from app.integrations.channels.phone import PhoneAdapter
from app.integrations.channels.simulated import SimulatedChatAdapter
from app.integrations.channels.whatsapp import WhatsAppAdapter
from app.shared.errors import DomainError


class ChannelGateway:
    def resolve(self, channel):
        if channel == "simulated_chat":
            return SimulatedChatAdapter()
        unavailable = {"whatsapp": WhatsAppAdapter, "email": EmailAdapter, "phone": PhoneAdapter}
        if channel in unavailable:
            return unavailable[channel]()
        raise DomainError("channel_unavailable", "Unsupported channel", 422)
