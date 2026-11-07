"""Resource-bound opaque demo credential. No Hermes credential is accepted here."""

import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from starlette.responses import JSONResponse


@dataclass(frozen=True)
class Principal:
    principal_id: str
    issuer: str = "collectflow-local"
    audience: str = "/mcp"
    scopes: frozenset[str] = frozenset({"collection:read", "collection:write"})


current_principal: ContextVar[Principal | None] = ContextVar("mcp_principal", default=None)


class OpaqueTokenRegistry:
    """One explicitly provisioned opaque credential, with server-owned metadata.

    Opaque tokens carry no client-supplied issuer/audience/scope claims. This registry
    is only used by the MCP resource, and its record binds all three plus expiry.
    Remote multi-tenant OAuth provisioning remains outside the synthetic demo.
    """

    def __init__(self, token: str, principal_id: str, expires_at: datetime | None):
        if not token.isascii():
            raise ValueError("MCP credential must be ASCII")
        if expires_at is not None and expires_at.utcoffset() is None:
            raise ValueError("MCP token expiry must have a timezone")
        self._token = token
        self._principal = Principal(principal_id)
        self._expires_at = expires_at

    def verify(self, authorization: str) -> Principal | None:
        if not self._token or not self._expires_at:
            return None
        if datetime.now(UTC) >= self._expires_at:
            return None
        scheme, _, credential = authorization.partition(" ")
        if (
            scheme.lower() != "bearer"
            or not credential.isascii()
            or not secrets.compare_digest(credential, self._token)
        ):
            return None
        return self._principal


class BearerPrincipalMiddleware:
    def __init__(self, app, registry: OpaqueTokenRegistry):
        self.app = app
        self.registry = registry

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/mcp":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        principal = self.registry.verify(headers.get(b"authorization", b"").decode("latin1"))
        if principal is None:
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="collectflow-mcp"'},
            )
            return await response(scope, receive, send)
        context_token = current_principal.set(principal)
        try:
            return await self.app(scope, receive, send)
        finally:
            current_principal.reset(context_token)
