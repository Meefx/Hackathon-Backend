"""Render both supported placeholder contracts using authoritative account facts."""

import re

from app.shared.errors import DomainError
from app.trust.errors import TrustError
from app.trust.safety import render_candidate

_ALIASES = {"borrower_name": "borrower_first_name", "outstanding_amount": "amount_due"}
_LEGACY = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")


def render_message(body: str, account: dict) -> str:
    normalized = _LEGACY.sub(lambda match: "{{" + _ALIASES.get(match[1], match[1]) + "}}", body)
    facts = {
        "borrower_first_name": account["borrower_name"],
        "amount_due": f"Rp{account['outstanding_amount']:,}".replace(",", "."),
        "loan_reference": account["loan_reference"],
        "due_date": str(account["due_date"]),
        "lender_name": "Synthetic Lender",
        "product_name": "Demo Credit",
        "payment_instructions": "the official application",
    }
    try:
        return render_candidate(normalized, facts)
    except TrustError as exc:
        raise DomainError("result_invalid", exc.code) from exc
