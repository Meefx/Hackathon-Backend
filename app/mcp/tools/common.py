"""Concurrency-limited offloading and safe error mapping for domain tool adapters."""

import json
import logging
import time
from functools import partial

import anyio
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel

from app.mcp.auth import current_principal
from app.mcp.schemas.collection import Assignment
from app.shared.caller import Caller
from app.shared.errors import DomainError

logger = logging.getLogger(__name__)


def assigned_caller(request: Assignment, *, write: bool = False) -> Caller:
    principal = current_principal.get()
    required = "collection:write" if write else "collection:read"
    if principal is None or principal.audience != "/mcp" or required not in principal.scopes:
        raise ToolError(json.dumps({"code": "permission_denied", "message": "Tool access denied"}))
    return Caller(
        principal_id=principal.principal_id,
        actor_kind="agent",
        account_id=request.account_id,
        run_id=request.run_id,
        execution_attempt_id=request.execution_attempt_id,
        fence=request.fence,
    )


class DomainTools:
    def __init__(self, service, *, timeout_seconds: float = 20):
        self.service = service
        self.timeout_seconds = timeout_seconds
        self.limiter = anyio.CapacityLimiter(8)

    async def call(self, operation: str, output: type[BaseModel], *args, **kwargs):
        started = time.monotonic()
        outcome = "interrupted"
        try:
            # This async response budget does not forcibly stop a synchronous call.
            # Blocking psycopg runs off-loop; its connection/statement/lock limits
            # bound DB work. Cancellation waits for transaction cleanup rather than
            # abandoning a possible write; service idempotency handles lost responses.
            # Cleanup can exceed this budget; it is not a hard total-call deadline.
            with anyio.fail_after(self.timeout_seconds):
                result = await anyio.to_thread.run_sync(
                    partial(getattr(self.service, operation), *args, **kwargs),
                    limiter=self.limiter,
                )
                await anyio.lowlevel.checkpoint()
            validated = output.model_validate(result)
            outcome = "completed"
            return validated
        except DomainError as error:
            outcome = error.code
            raise ToolError(json.dumps(error.detail())) from None
        except TimeoutError:
            outcome = "dependency_timeout"
            raise ToolError(
                json.dumps({"code": "dependency_timeout", "message": "Tool timed out"})
            ) from None
        except Exception:  # noqa: BLE001 - transport boundary must redact dependency failures
            outcome = "dependency_unavailable"
            raise ToolError(
                json.dumps(
                    {"code": "dependency_unavailable", "message": "Tool could not be completed"}
                )
            ) from None
        finally:
            caller = kwargs.get("caller")
            logger.info(
                "MCP tool=%s principal=%r account_id=%r run_id=%r outcome=%s duration_ms=%d",
                operation,
                getattr(caller, "principal_id", None),
                getattr(caller, "account_id", None),
                getattr(caller, "run_id", None),
                outcome,
                int((time.monotonic() - started) * 1000),
            )
