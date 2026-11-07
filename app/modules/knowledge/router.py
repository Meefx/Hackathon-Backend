"""Synchronous producer HTTP routes, composed by the authenticated trust facade."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.modules.knowledge import schemas
from app.modules.knowledge.authorization import Actor


def create_router(service, actor, key) -> APIRouter:
    router = APIRouter()
    Who = Annotated[Actor, Depends(actor)]
    Key = Annotated[UUID, Depends(key)]
    Limit = Annotated[int, Query(ge=1, le=100)]
    Offset = Annotated[int, Query(ge=0, le=9223372036854775807)]

    @router.post("/knowledge/documents", response_model=schemas.IngestResult)
    def ingest(data: schemas.Document, who: Who, mutation_key: Key) -> dict[str, Any]:
        return service.ingest(who, mutation_key, data.model_dump(mode="json"))

    @router.get("/knowledge/documents", response_model=list[schemas.DocumentSummary])
    def documents(who: Who, limit: Limit = 100, offset: Offset = 0) -> list[dict]:
        return service.documents(who, limit, offset)

    @router.post("/knowledge/documents/{document_id}/qa", response_model=schemas.QAResult)
    def qa_document(document_id: UUID, who: Who, mutation_key: Key) -> dict[str, Any]:
        return service.qa_document(who, mutation_key, document_id)

    @router.post(
        "/knowledge/documents/{document_id}/approve", response_model=schemas.ArtifactResult
    )
    def approve_document(
        document_id: UUID, data: schemas.Note, who: Who, mutation_key: Key
    ) -> dict[str, Any]:
        return service.approve_document(who, mutation_key, document_id, data.note)

    @router.post(
        "/knowledge/documents/{document_id}/retire", response_model=schemas.RetirementResult
    )
    def retire_document(
        document_id: UUID, data: schemas.Note, who: Who, mutation_key: Key
    ) -> dict[str, Any]:
        return service.retire_document(who, mutation_key, document_id, data.note)

    @router.post("/knowledge/search", response_model=schemas.SearchResult)
    def search(data: schemas.Retrieval, who: Who, mutation_key: Key) -> dict[str, Any]:
        # Keep the request key contract; never replay a cached retrieval result.
        return service.search(who, data.query, data.limit)

    @router.get("/knowledge/graph", response_model=schemas.DependencyGraph)
    def graph(who: Who, limit: Limit = 100, offset: Offset = 0) -> dict[str, Any]:
        return service.graph(who, limit, offset)

    @router.post("/templates", response_model=schemas.TemplateResult)
    def template(data: schemas.Template, who: Who, mutation_key: Key) -> dict[str, Any]:
        return service.create_template(who, mutation_key, data.model_dump(mode="json"))

    @router.get("/templates", response_model=list[schemas.TemplateSummary])
    def templates(who: Who, limit: Limit = 100, offset: Offset = 0) -> list[dict]:
        return service.templates(who, limit, offset)

    @router.post("/templates/{template_id}/qa", response_model=schemas.QAResult)
    def qa_template(template_id: UUID, who: Who, mutation_key: Key) -> dict[str, Any]:
        return service.qa_template(who, mutation_key, template_id)

    @router.post("/templates/{template_id}/approve", response_model=schemas.ArtifactResult)
    def approve_template(
        template_id: UUID, data: schemas.Note, who: Who, mutation_key: Key
    ) -> dict[str, Any]:
        return service.approve_template(who, mutation_key, template_id, data.note)

    return router
