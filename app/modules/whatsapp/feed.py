"""Read the operator's persisted WhatsApp outbound history without contacting WAHA."""

from typing import Any

from app.config import Settings
from app.db.connection import connect
from app.modules.conversations.schemas import Message
from app.modules.whatsapp import repository
from app.modules.whatsapp.service import require_operator
from app.shared.caller import Caller
from app.shared.errors import DomainError


def list_outbound(
    settings: Settings, caller: Caller, limit: int = 10, offset: int = 0
) -> dict[str, Any]:
    require_operator(caller)
    if caller.account_id:
        raise DomainError("assignment_denied", "Portfolio operator permission is required", 403)
    with connect(settings.database_url) as connection:
        page = repository.outbound_messages(connection, limit, offset)
    return {
        "items": [
            {
                "borrower_name": row["borrower_name"],
                "loan_reference": row["loan_reference"],
                "message": Message.model_validate(
                    {**row, "status": row["delivery_status"]}
                ).model_dump(mode="json"),
            }
            for row in page["items"]
        ],
        "total": page["total"],
        "limit": limit,
        "offset": offset,
    }
