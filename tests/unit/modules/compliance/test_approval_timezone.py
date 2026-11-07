"""The exact approval binding survives a timestamptz timezone conversion."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.modules.compliance.service import binding_for, sign_binding


def binding(expiry):
    return binding_for(
        {
            "id": "synthetic-message",
            "version": 1,
            "text": "Synthetic reminder",
            "account_id": "synthetic-account",
            "citation_ids": [],
            "channel": "simulated_chat",
        },
        {"recipient_id": "synthetic-recipient", "version": 1},
        {"policy_version": "synthetic-policy", "authority_hash": "synthetic-authority"},
        {"snapshot_hash": "synthetic-snapshot", "references": []},
        expiry,
    )


@pytest.mark.parametrize(
    "zone", ["UTC", "Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura", "America/New_York"]
)
def test_same_expiry_has_identical_binding_and_signature(zone):
    expiry = datetime(2026, 11, 7, 3, 2, tzinfo=UTC)
    original = binding(expiry)
    reread = binding(expiry.astimezone(ZoneInfo(zone)))
    assert original == reread
    assert sign_binding(original, "synthetic-test-key") == sign_binding(
        reread, "synthetic-test-key"
    )
    assert sign_binding(original, "synthetic-test-key") != sign_binding(
        binding(expiry + timedelta(seconds=1)), "synthetic-test-key"
    )


def test_timezone_missing_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        binding(datetime(2026, 11, 7, 3, 2))
