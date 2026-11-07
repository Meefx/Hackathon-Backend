from mcp.types import ToolAnnotations

from app.mcp.schemas.collection import ContextRequest, KnowledgeSnapshot
from app.mcp.tools.common import assigned_caller


def register(server, tools):
    @server.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
    )
    async def retrieve_approved_context(request: ContextRequest) -> KnowledgeSnapshot:
        """Retrieve 1–5 approved snippets and persist versioned run evidence.

        Critical sources include bounded full context and a verification profile.
        Requires an active account assignment and a timezone-aware deadline.
        """
        query = request.model_dump(
            exclude={"account_id", "run_id", "execution_attempt_id", "fence"}
        )
        return await tools.call(
            "retrieve_approved_context",
            KnowledgeSnapshot,
            request.run_id,
            query,
            caller=assigned_caller(request, write=True),
        )
