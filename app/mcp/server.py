"""Standalone authenticated MCP-only ingress, intentionally separate from operator REST."""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.config import Settings, get_settings
from app.mcp.auth import BearerPrincipalMiddleware, OpaqueTokenRegistry
from app.mcp.tools import accounts, conversations, knowledge, manager, reviews
from app.mcp.tools.common import DomainTools


def create_app(settings: Settings | None = None, service=None, manager_service=None):
    """uvicorn app.mcp.server:create_app --factory --host 127.0.0.1 --port 8002."""
    settings = settings or get_settings()
    if service is None:
        from app.modules.collection.service import CollectionService

        service = CollectionService(settings)
    if manager_service is None:
        from app.modules.manager.chat_service import ManagerChatService

        manager_service = ManagerChatService(settings)
    server = FastMCP(
        "CollectFlow collection tools",
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=64_000,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=settings.mcp_allowed_origins,
        ),
    )
    tools = DomainTools(service)
    for module in (accounts, knowledge, conversations, reviews):
        module.register(server, tools)
    manager.register(server, DomainTools(manager_service))
    app = server.streamable_http_app()
    app.add_middleware(
        BearerPrincipalMiddleware,
        registry=OpaqueTokenRegistry(
            settings.mcp_bearer_token.get_secret_value(),
            settings.mcp_principal_id,
            settings.mcp_token_expires_at,
        ),
    )
    # SDK-owned lifespan starts/stops its session manager, even in stateless mode.
    app.state.mcp = server
    return app
