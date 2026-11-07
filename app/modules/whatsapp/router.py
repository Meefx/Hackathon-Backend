from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.modules.conversations.schemas import DispatchRequest, DispatchResult
from app.modules.whatsapp import delivery, service, webhooks
from app.modules.whatsapp.schemas import ConnectionStatus, LinkRequest, WebhookResult
from app.shared.caller import Caller
from app.shared.clock import utcnow
from app.shared.errors import DomainError

router = APIRouter(prefix="/api/v1", tags=["WhatsApp"])


@router.get("/accounts/{account_id}/whatsapp", response_model=ConnectionStatus)
def connection_status(account_id: str):
    return service.status(get_settings(), account_id, Caller())


@router.post("/accounts/{account_id}/whatsapp", response_model=ConnectionStatus)
def link_account(account_id: str, request: LinkRequest):
    return service.link(get_settings(), account_id, request, Caller(), utcnow())


@router.post("/accounts/{account_id}/whatsapp/disconnect", response_model=ConnectionStatus)
def disconnect_account(account_id: str):
    return service.disconnect(get_settings(), account_id, Caller(), utcnow())


@router.post("/messages/{message_id}/dispatch", response_model=DispatchResult)
def dispatch(message_id: str, request: DispatchRequest):
    return delivery.dispatch(get_settings(), message_id, request, Caller(), utcnow)


@router.post("/webhooks/waha", response_model=WebhookResult)
async def receive_webhook(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 262144:
            raise DomainError("webhook_too_large", "Webhook exceeds the size limit", 413)
    return await run_in_threadpool(
        webhooks.receive,
        get_settings(),
        bytes(body),
        request.headers.get("X-Webhook-Hmac", ""),
        request.headers.get("X-Webhook-Hmac-Algorithm", ""),
        utcnow(),
    )
