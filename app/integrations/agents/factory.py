"""Provider selection is explicit; unavailable Hermes never falls back to simulation."""

from app.config import Settings
from app.integrations.hermes.client import HermesClient


def create_hermes_runtime(settings: Settings) -> HermesClient:
    return HermesClient(
        settings.hermes_api_base_url,
        settings.hermes_api_key.get_secret_value(),
        profile=settings.hermes_profile,
        timeout=settings.hermes_timeout_seconds,
    )
