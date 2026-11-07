from mcp.types import ToolAnnotations

from app.mcp.schemas.collection import Assignment, HistoryPage, HistoryRequest
from app.mcp.tools.common import assigned_caller
from app.modules.cases.schemas import PolicyDecision
from app.schemas import Account


def register(server, tools):
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)

    @server.tool(annotations=read)
    async def get_account(request: Assignment) -> Account:
        """Read assigned synthetic account facts; amounts are integer IDR. Active run required."""
        return await tools.call(
            "get_account", Account, request.account_id, caller=assigned_caller(request)
        )

    @server.tool(annotations=read)
    async def get_contact_policy(request: Assignment) -> PolicyDecision:
        """Read backend eligibility for the assigned account. This grants no dispatch permission."""
        return await tools.call(
            "get_contact_policy",
            PolicyDecision,
            request.account_id,
            caller=assigned_caller(request),
        )

    @server.tool(annotations=read)
    async def get_contact_history(request: HistoryRequest) -> HistoryPage:
        """Read at most 100 assigned account messages, paginated by offset; never sends a message."""
        return await tools.call(
            "get_contact_history",
            HistoryPage,
            request.account_id,
            caller=assigned_caller(request),
            limit=request.limit,
            offset=request.offset,
        )
