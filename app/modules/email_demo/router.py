from typing import Any
from uuid import UUID

from fastapi import APIRouter

from app.config import get_settings
from app.modules.email_demo import service
from app.modules.email_demo.schemas import EmailDemo, EmailDemoRequest, EmailDemoStatus
from app.shared.caller import Caller
from app.shared.clock import utcnow

router = APIRouter(prefix="/api/v1/integrations/gmail/collection-demo", tags=["Gmail demo"])


@router.get("", response_model=EmailDemoStatus)
def demo_status() -> dict[str, Any]:
    return service.status(get_settings(), Caller(), utcnow())


@router.post("", response_model=EmailDemo, status_code=202)
def start_demo(request: EmailDemoRequest) -> dict[str, Any]:
    return service.start_demo(get_settings(), request, Caller(), utcnow())


@router.get("/{demo_id}", response_model=EmailDemo)
def get_demo(demo_id: UUID) -> dict[str, Any]:
    return service.get_demo(get_settings(), str(demo_id), Caller(), utcnow())
