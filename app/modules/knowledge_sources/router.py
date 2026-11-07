"""Reviewer-only HTTP adapter for archived public regulatory sources."""

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.modules.knowledge_sources import schemas
from app.modules.knowledge_sources.service import SourceService
from app.trust.security import Actor


def create_router(service: SourceService, actor_dependency: Callable[..., Actor]) -> APIRouter:
    router = APIRouter(prefix="/knowledge/sources", tags=["Source archive review"])
    Who = Annotated[Actor, Depends(actor_dependency)]
    Limit = Annotated[int, Query(ge=1, le=100)]
    Offset = Annotated[int, Query(ge=0, le=9223372036854775807)]

    @router.get("", response_model=schemas.SourcePage)
    def sources(who: Who, limit: Limit = 25, offset: Offset = 0) -> dict[str, Any]:
        return service.list_sources(who, limit, offset)

    @router.get("/{source_id}", response_model=schemas.SourceDetail)
    def source_detail(source_id: UUID, who: Who) -> dict[str, Any]:
        return service.detail(who, source_id)

    @router.get(
        "/{source_id}/download",
        response_class=Response,
        responses={
            200: {
                "description": "Original archived PDF, restricted to source reviewers.",
                "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
                "headers": {
                    "X-Content-SHA256": {"schema": {"type": "string"}},
                    "ETag": {"schema": {"type": "string"}},
                },
            }
        },
    )
    def source_download(source_id: UUID, who: Who) -> Response:
        body, digest = service.download(who, source_id)
        return Response(
            content=body,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="source-{source_id}.pdf"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
                "X-Content-SHA256": digest,
                "ETag": f'"{digest}"',
            },
        )

    return router
