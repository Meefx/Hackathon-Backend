"""Fenced, advisory manager access to the synthetic portfolio."""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg import Connection
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.db.connection import connect
from app.modules.accounts.service import get_account, list_accounts
from app.modules.compliance.service import evaluate_contact_policy
from app.modules.conversations.redaction import sanitize_conversation
from app.modules.dashboard.service import summarize
from app.modules.manager import chat_repository as repository
from app.modules.manager.chat_schemas import ManagerChatRequest
from app.modules.manager.schemas import TaskProposal
from app.modules.runs.service import (
    authorize_portfolio_assignment,
    record_advisory_event,
    start_run_in_transaction,
)
from app.shared.caller import Caller
from app.shared.clock import utcnow
from app.shared.errors import DomainError
from app.shared.idempotency import record, replay


class ManagerChatService:
    def __init__(
        self, settings: Settings | None = None, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self.settings = settings or get_settings()
        self.clock = clock

    def start_run(self, request: ManagerChatRequest, caller: Caller) -> dict[str, Any]:
        with connect(self.settings.database_url) as connection:
            return start_run_in_transaction(
                connection, self.settings, None, request, caller, self.clock()
            )

    def list_sessions(self, caller: Caller, limit: int = 20, offset: int = 0) -> dict:
        self._operator(caller)
        self._pagination(limit, offset)
        with connect(self.settings.database_url) as connection:
            return repository.list_sessions(connection, caller.principal_id, limit, offset)

    def get_session(self, session_id: str, caller: Caller) -> dict[str, Any]:
        self._operator(caller)
        with connect(self.settings.database_url) as connection:
            return repository.get_session(connection, session_id, caller.principal_id)

    def recover_request(self, key: UUID | str, caller: Caller) -> dict:
        self._operator(caller)
        with connect(self.settings.database_url) as connection:
            return repository.recover_request(connection, caller.principal_id, str(key))

    def _authorize(
        self, connection: Connection[dict[str, Any]], run_id: str, caller: Caller, now: datetime
    ) -> dict:
        return authorize_portfolio_assignment(connection, run_id, caller, now)

    def _accounts(self, now: datetime) -> list[dict]:
        return list_accounts(
            self.settings.database_url, now.astimezone(ZoneInfo("Asia/Jakarta")).date()
        )

    @staticmethod
    def _operator(caller: Caller) -> None:
        if caller.actor_kind != "operator":
            raise DomainError("assignment_denied", "Operator access required", 403)

    @staticmethod
    def _pagination(limit: int, offset: int) -> None:
        if not 1 <= limit <= 100 or offset < 0:
            raise DomainError("invalid_input", "Invalid pagination", 422)

    def overview(self, run_id: str, caller: Caller) -> dict:
        now = self.clock()
        with connect(self.settings.database_url) as connection:
            self._authorize(connection, run_id, caller, now)
            return summarize(self._accounts(now))

    def search_accounts(
        self,
        run_id: str,
        query: str = "",
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
        caller: Caller | None = None,
    ) -> dict:
        self._pagination(limit, offset)
        if len(query) > 100 or status not in (None, "current", "early", "late", "critical"):
            raise DomainError("invalid_input", "Invalid account search", 422)
        now = self.clock()
        with connect(self.settings.database_url) as connection:
            self._authorize(connection, run_id, caller or Caller(), now)
            needle = query.strip().casefold()
            items = [
                account
                for account in self._accounts(now)
                if (not status or account["status"] == status)
                and any(
                    needle in account[field].casefold()
                    for field in ("id", "borrower_name", "loan_reference")
                )
            ]
            return {
                "items": items[offset : offset + limit],
                "total": len(items),
                "limit": limit,
                "offset": offset,
            }

    def _inspect(
        self, connection: Connection[dict[str, Any]], account_id: str, now: datetime
    ) -> dict:
        account = get_account(
            self.settings.database_url, account_id, now.astimezone(ZoneInfo("Asia/Jakarta")).date()
        )
        if not account:
            raise DomainError("not_found", "Account not found", 404)
        identifiers = (account_id, account["borrower_name"], account["loan_reference"])
        return {
            "account": account,
            "policy": evaluate_contact_policy(connection, account_id, now),
            "recent_messages": [
                {**message, "text": sanitize_conversation(message["text"], identifiers)[:800]}
                for message in repository.recent_messages(connection, account_id)
            ],
            "open_reviews": repository.open_reviews(connection, account_id),
        }

    def inspect_account(self, run_id: str, account_id: str, caller: Caller) -> dict:
        now = self.clock()
        with connect(self.settings.database_url) as connection:
            self._authorize(connection, run_id, caller, now)
            return self._inspect(connection, account_id, now)

    def context_for_run(self, run_id: str, caller: Caller) -> dict:
        now = self.clock()
        with connect(self.settings.database_url) as connection:
            run = self._authorize(connection, run_id, caller, now)
            accounts = self._accounts(now)
            account_id = run["payload"].get("context_account_id")
            return {
                "overview": summarize(accounts),
                "accounts_page": {
                    "items": accounts[:20],
                    "total": len(accounts),
                    "limit": 20,
                    "offset": 0,
                },
                "selected_account": self._inspect(connection, account_id, now)
                if account_id
                else None,
            }

    def submit_response(
        self, run_id: str, text: str, caller: Caller, idempotency_key: UUID | str
    ) -> dict:
        if not text.strip() or len(text) > 4000:
            raise DomainError("invalid_input", "Response must contain 1–4000 characters", 422)
        return self._artifact(run_id, "manager_response", text, caller, idempotency_key)

    def propose_task(
        self,
        run_id: str,
        account_id: str,
        task_type: str,
        instruction: str,
        caller: Caller,
        idempotency_key: UUID | str,
    ) -> dict:
        try:
            proposal = TaskProposal.model_validate(
                {"account_id": account_id, "task_type": task_type, "instruction": instruction}
            ).model_dump(mode="json")
        except ValidationError as error:
            raise DomainError("invalid_input", "Invalid account task proposal", 422) from error
        return self._artifact(
            run_id, "manager_task_proposal", instruction, caller, idempotency_key, proposal
        )

    def _artifact(
        self,
        run_id: str,
        kind: str,
        text: str,
        caller: Caller,
        idempotency_key: UUID | str,
        proposal: dict | None = None,
    ) -> dict:
        now = self.clock()
        key = str(idempotency_key)
        payload = {"kind": kind, "text": text, "proposal": proposal}
        scope = "manager_chat_artifact:" + run_id
        with connect(self.settings.database_url) as connection:
            self._authorize(connection, run_id, caller, now)
            if previous := replay(connection, scope, key, payload):
                return previous
            if proposal:
                repository.require_context_account(connection, proposal["account_id"])
            artifact = repository.add_artifact(connection, run_id, kind, text, now, proposal)
            artifact["created_at"] = artifact["created_at"].isoformat()
            record_advisory_event(connection, run_id, now)
            record(connection, scope, key, payload, artifact)
            return artifact
