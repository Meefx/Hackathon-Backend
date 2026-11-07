"""Local operator HTTP entry points for portfolio manager conversations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.modules.manager.chat_schemas import ManagerChatRequest
from app.modules.manager.chat_service import ManagerChatService
from app.modules.manager.schemas import ManagerSession
from app.modules.runs.schemas import RunAccepted
from app.shared.caller import Caller
from app.shared.schemas import Page

router = APIRouter(prefix="/api/v1/manager/chat", tags=["Manager chat"])


@router.post("/runs", response_model=RunAccepted, status_code=202)
def start_chat(request: ManagerChatRequest):
    return ManagerChatService().start_run(request, Caller())


@router.get("/sessions", response_model=Page[ManagerSession])
def list_sessions(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return ManagerChatService().list_sessions(Caller(), limit, offset)


@router.get("/sessions/{session_id}", response_model=ManagerSession)
def get_session(session_id: UUID):
    return ManagerChatService().get_session(str(session_id), Caller())


@router.get("/requests/{key}", response_model=RunAccepted)
def recover_request(key: UUID):
    return ManagerChatService().recover_request(key, Caller())
