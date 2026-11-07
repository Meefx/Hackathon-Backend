from mcp.types import ToolAnnotations

from app.mcp.schemas.collection import ReviewRequest
from app.mcp.tools.common import assigned_caller
from app.modules.reviews.schemas import ReviewItem


def register(server, tools):
    @server.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
    )
    async def request_human_review(request: ReviewRequest) -> ReviewItem:
        """Create assigned-case human review/pause using a UUID replay key; cannot approve/resume/dispatch."""
        payload = request.model_dump(exclude={"account_id", "execution_attempt_id", "fence"})
        return await tools.call(
            "request_human_review",
            ReviewItem,
            request.account_id,
            payload,
            caller=assigned_caller(request, write=True),
        )
