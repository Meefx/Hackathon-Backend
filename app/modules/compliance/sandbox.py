"""Server-owned scope for Hermes drafts delivered to synthetic test recipients."""

from typing import Any

from psycopg import Connection

from app.config import Settings
from app.modules.compliance.repository import sandbox_context
from app.modules.email_demo.service import matches_test_recipient as email_recipient
from app.modules.whatsapp.service import matches_test_recipient as whatsapp_recipient


def hermes_sandbox(
    connection: Connection[dict[str, Any]], settings: Settings, message: dict[str, Any]
) -> bool:
    if not settings.demo_fixture_enabled or not message["text"].startswith("[DEMO SINTETIS] "):
        return False
    run = sandbox_context(connection, message.get("run_id"), message["account_id"])
    if (
        not run
        or run["requested_runtime_mode"] != "hermes"
        or run["caller_id"] != "local-operator"
        or run["account_id"] != message["account_id"]
        or run["payload"]["task_type"] != "follow_up"
        or not run["is_synthetic"]
        or not run["recipient_verified"]
    ):
        return False
    if message["channel"] == "whatsapp":
        return whatsapp_recipient(connection, settings, message["account_id"], run["recipient_id"])
    if message["channel"] == "email":
        return email_recipient(connection, settings, run, run["recipient_id"])
    return False
