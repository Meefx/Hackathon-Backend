from mcp.types import ToolAnnotations

from app.mcp.schemas.collection import ArtifactRequest, DraftRequest
from app.mcp.tools.common import assigned_caller
from app.modules.conversations.schemas import Message
from app.modules.runs.schemas import RunArtifact


def register(server, tools):
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)

    @server.tool(annotations=write)
    async def submit_draft_candidate(request: DraftRequest) -> Message:
        """Persist placeholder draft/citations for active assignment with UUID replay key. Never dispatches."""
        return await tools.call(
            "submit_draft_candidate",
            Message,
            request.candidate,
            caller=assigned_caller(request, write=True),
        )

    @server.tool(annotations=write)
    async def submit_run_artifact(request: ArtifactRequest) -> RunArtifact:
        """Persist task-appropriate QA, copilot brief or improvement proposal; advisory only. UUID replay key required."""
        return await tools.call(
            "submit_run_artifact",
            RunArtifact,
            request.candidate,
            caller=assigned_caller(request, write=True),
        )
