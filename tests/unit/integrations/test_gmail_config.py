import pytest
from pydantic import ValidationError

from app.config import Settings


def test_gmail_credentials_are_backend_only_and_masked():
    settings = Settings(
        gmail_client_id="test-client.apps.googleusercontent.com",
        gmail_client_secret="synthetic-client-secret",
        gmail_refresh_token="synthetic-refresh-token",
    )
    assert settings.gmail_client_id == "test-client.apps.googleusercontent.com"
    assert "synthetic-client-secret" not in repr(settings)
    assert "synthetic-refresh-token" not in repr(settings)


@pytest.mark.parametrize(
    "values",
    [
        {"gmail_timeout_seconds": 0},
        {"gmail_timeout_seconds": 61},
        {"gmail_oauth_port": 0},
        {"gmail_oauth_port": 65536},
    ],
)
def test_gmail_configuration_is_bounded(values):
    with pytest.raises(ValidationError):
        Settings(**values)
