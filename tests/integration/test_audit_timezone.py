"""Audit hashes bind UTC instants independently of PostgreSQL's session timezone."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.db.connection import connect
from app.modules.audit.service import append_event, verify_chain
from app.seed import seed


@pytest.mark.parametrize("zone", ["UTC", "Asia/Jakarta", "America/New_York"])
def test_audit_round_trip_preserves_hash_and_detects_tampering(database_url, zone):
    now = datetime(2026, 11, 7, 3, tzinfo=UTC)
    seed(now.date())
    with connect(database_url) as connection:
        event = append_event(
            connection,
            "CF-1001",
            "timezone_test",
            "operator",
            "observed",
            now.astimezone(ZoneInfo(zone)),
        )
        assert event["created_at"] == now.isoformat()
        assert verify_chain([event])
        connection.execute("SELECT set_config('TimeZone', %s, true)", (zone,))
        row = connection.execute(
            "SELECT * FROM audit_events WHERE id=%s", (event["id"],)
        ).fetchone()
        assert verify_chain([row])
        assert not verify_chain([{**row, "created_at": row["created_at"] + timedelta(seconds=1)}])
        assert not verify_chain([{**row, "outcome": "tampered"}])
        assert not verify_chain([{**row, "created_at": now.replace(tzinfo=None)}])
