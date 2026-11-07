"""Authoritative context for non-outbound human review discussions."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from psycopg import Connection

from app.config import Settings
from app.modules.accounts.service import get_account
from app.modules.compliance.service import evaluate_contact_policy
from app.modules.conversations.redaction import sanitize_conversation
from app.modules.manager import repository
from app.modules.reviews.service import get_account_review
from app.shared.errors import DomainError


def discussion_context(
    connection: Connection[dict[str, Any]],
    settings: Settings,
    account_id: str,
    review_id: str,
    now: datetime,
) -> dict[str, Any]:
    review = get_account_review(connection, account_id, review_id)
    account = get_account(
        settings.database_url, account_id, now.astimezone(ZoneInfo("Asia/Jakarta")).date()
    )
    if account is None:
        raise DomainError("not_found", "Account not found", 404)
    decision = evaluate_contact_policy(connection, account_id, now)
    identifiers = (account_id, account["borrower_name"], account["loan_reference"])
    messages = [
        {**message, "text": sanitize_conversation(message["text"], identifiers)[:800]}
        for message in repository.recent_messages(connection, account_id)
    ]
    return {
        "account_id": account_id,
        "outstanding_amount": account["outstanding_amount"],
        "currency": "IDR",
        "due_date": account["due_date"],
        "days_past_due": account["days_past_due"],
        "review": {
            "id": review["id"],
            "reason": review["reason"],
            "status": review["status"],
            "resolution_note": sanitize_conversation(review["resolution_note"])[:1000]
            if review["resolution_note"]
            else "",
        },
        "contact_state": decision["contact_state"],
        "contact_allowed": decision["allowed"],
        "reason_codes": decision["reason_codes"],
        "originating_run": repository.originating_run(connection, review["run_id"]),
        "recent_messages": messages,
    }


def simulated_discussion(context: dict[str, Any]) -> str:
    review = context["review"]
    guidance = {
        "hardship": "Tinjau kemampuan bayar dan kebutuhan bantuan peminjam bersama petugas.",
        "dispute": "Verifikasi sengketa dan bukti pembayaran sebelum menentukan tindak lanjut.",
        "human_request": "Tugaskan petugas manusia untuk menindaklanjuti permintaan peminjam.",
        "operator_takeover": "Tinjau alasan pengambilalihan dan hasil penanganan petugas.",
    }
    lines = [
        "Analisis Manager (simulasi)",
        (
            f"Akun {context['account_id']} memiliki tagihan Rp{context['outstanding_amount']:,} "
            f"dan keterlambatan {context['days_past_due']} hari."
        ),
        (
            f"Review {review['reason']} berstatus {review['status']}. "
            f"Status kontak {context['contact_state']}."
        ),
        guidance.get(review["reason"], "Tinjau konteks kasus bersama petugas."),
    ]
    if context["recent_messages"]:
        lines.append("Pesan terakhir peminjam/percakapan " + context["recent_messages"][-1]["text"])
    if review["resolution_note"]:
        lines.append("Catatan keputusan petugas " + review["resolution_note"])
    lines.append(
        "Catat keputusan melalui pilihan penyelesaian review. Backend mengevaluasi kelayakan "
        "kontak kembali saat petugas meminta resume dan pada jadwal penagihan berikutnya."
    )
    return "\n\n".join(lines)


def reserve_run_session(
    connection: Connection[dict[str, Any]],
    session_id: str,
    account_id: str | None,
    caller_id: str,
    instruction: str,
    now: datetime,
    existing: bool,
    context_account_id: str | None = None,
) -> int:
    """Reserve one ordered turn inside the run admission transaction."""
    from app.modules.manager import chat_repository

    if account_id is None:
        chat_repository.require_context_account(connection, context_account_id)
    if existing:
        chat_repository.require_session(connection, session_id, account_id, caller_id)
    else:
        chat_repository.create_session(
            connection, session_id, account_id, caller_id, instruction[:80], now
        )
    turn = chat_repository.next_turn(connection, session_id)
    if account_id is None:
        chat_repository.update_context(connection, session_id, context_account_id)
    return turn
