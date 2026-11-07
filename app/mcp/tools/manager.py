"""Manager portfolio tools delegate authorization and advisory writes to the domain."""

from mcp.types import ToolAnnotations

from app.mcp.schemas.manager import (
    AccountTaskProposalRequest,
    ManagerResponseRequest,
    PortfolioAccountContext,
    PortfolioAccountPage,
    PortfolioAccountRequest,
    PortfolioAssignment,
    PortfolioSearchRequest,
)
from app.mcp.tools.common import portfolio_caller
from app.modules.runs.schemas import RunArtifact
from app.schemas import Dashboard


def register(server, tools):
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
    advisory = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)

    @server.tool(annotations=read)
    async def get_portfolio_overview(request: PortfolioAssignment) -> Dashboard:
        """Read portfolio totals and aging buckets in integer IDR for an active manager-chat run."""
        return await tools.call(
            "overview", Dashboard, request.run_id, caller=portfolio_caller(request)
        )

    @server.tool(annotations=read)
    async def search_portfolio_accounts(request: PortfolioSearchRequest) -> PortfolioAccountPage:
        """Search synthetic portfolio accounts by query/status, with at most 100 results per page."""
        return await tools.call(
            "search_accounts",
            PortfolioAccountPage,
            request.run_id,
            query=request.query,
            status=request.status,
            limit=request.limit,
            offset=request.offset,
            caller=portfolio_caller(request),
        )

    @server.tool(annotations=read)
    async def inspect_portfolio_account(request: PortfolioAccountRequest) -> PortfolioAccountContext:
        """Read one portfolio account, contact policy, up to 5 redacted messages and 20 open reviews."""
        return await tools.call(
            "inspect_account",
            PortfolioAccountContext,
            request.run_id,
            request.account_id,
            caller=portfolio_caller(request),
        )

    @server.tool(annotations=advisory)
    async def submit_manager_response(request: ManagerResponseRequest) -> RunArtifact:
        """Save the manager-chat answer as an artifact using a UUID replay key. Never dispatches."""
        return await tools.call(
            "submit_response",
            RunArtifact,
            request.run_id,
            request.text,
            caller=portfolio_caller(request, write=True),
            idempotency_key=request.idempotency_key,
        )

    @server.tool(annotations=advisory)
    async def propose_account_task(request: AccountTaskProposalRequest) -> RunArtifact:
        """Save an advisory account-task proposal with a UUID replay key. Operator starts the task."""
        return await tools.call(
            "propose_task",
            RunArtifact,
            request.run_id,
            request.account_id,
            request.task_type,
            request.instruction,
            caller=portfolio_caller(request, write=True),
            idempotency_key=request.idempotency_key,
        )
