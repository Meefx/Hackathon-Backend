from datetime import UTC, datetime

import pytest

from app.modules.compliance.rendering import render_message
from app.modules.compliance.screening import screen_candidate
from app.shared.errors import DomainError

ACCOUNT = {
    "borrower_name": "Demo Borrower",
    "outstanding_amount": 1250000,
    "loan_reference": "LN-DEMO",
    "due_date": datetime(2026, 11, 1, tzinfo=UTC).date(),
}


def test_existing_and_native_placeholders_share_server_facts():
    assert render_message("Balance {outstanding_amount} due {due_date}.", ACCOUNT) == (
        "Balance Rp1.250.000 due 2026-11-01."
    )
    assert render_message("Your {{product_name}} payment is {{amount_due}}.", ACCOUNT) == (
        "Your Demo Credit payment is Rp1.250.000."
    )


def test_template_cannot_embed_unsourced_numbers():
    with pytest.raises(DomainError, match="UNSOURCED_NUMBER"):
        render_message("Pay 500 today.", ACCOUNT)


@pytest.mark.parametrize("text", ["We will contact your family.", "Pay now!", "You are stupid."])
def test_native_deterministic_guardrails_apply_to_collection(text):
    assert screen_candidate(text, mode="simulated", fixture_enabled=True)["result"] == "BLOCK"


def test_live_classifier_cannot_use_fixture_allowance():
    result = screen_candidate("Reply for assistance.", mode="live", fixture_enabled=True)
    assert result["result"] == "BLOCK"
    assert result["reason_codes"] == ["GUARDRAIL_UNAVAILABLE"]
