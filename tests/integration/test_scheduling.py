"""Daily scheduling must survive duplicate ticks without bypassing delivery policy."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.config import get_settings
from app.db.connection import connect
from app.modules.conversations.dispatch import simulate_dispatch
from app.modules.conversations.schemas import DispatchRequest
from app.modules.reviews.service import open_review
from app.modules.runs.schemas import StartRunRequest
from app.modules.runs.service import start_run
from app.seed import seed
from app.shared.caller import Caller
from app.shared.errors import DomainError
from app.workers.collection import process_once

NOW = datetime(2026, 11, 7, 2, tzinfo=UTC)  # Saturday 09.00 WIB.


def setup_accounts(database_url):
    seed(date(2026, 11, 7))
    with connect(database_url) as connection:
        connection.execute("UPDATE accounts SET outstanding_amount=0 WHERE id='CF-1006'")
    return get_settings()


def test_daily_boundary_and_positive_overdue_filter(database_url):
    from app.modules.scheduling.service import status, tick

    settings = setup_accounts(database_url)
    tick(settings, NOW - timedelta(seconds=1))
    assert status(settings, NOW)["counts"]["queued"] == 0
    tick(settings, NOW)
    with connect(database_url) as connection:
        rows = connection.execute(
            "SELECT account_id FROM collection_runs ORDER BY account_id"
        ).fetchall()
    assert [row["account_id"] for row in rows] == [
        "CF-1001",
        "CF-1002",
        "CF-1003",
        "CF-1004",
        "CF-1005",
    ]
    assert status(settings, NOW)["counts"]["queued"] == 5


def test_concurrent_ticks_and_restarts_preserve_one_daily_run(database_url):
    from app.modules.scheduling.service import tick

    settings = setup_accounts(database_url)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: tick(settings, NOW), range(2)))
    tick(settings, NOW)
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 5
        assert (
            connection.execute("SELECT count(*) AS n FROM collection_schedule_items").fetchone()[
                "n"
            ]
            == 5
        )


def test_completed_scheduled_draft_is_dispatched_exactly_once(database_url):
    from app.modules.scheduling.service import status, tick

    settings = setup_accounts(database_url)
    tick(settings, NOW)
    assert process_once(settings, now=NOW)
    tick(settings, NOW)
    tick(settings, NOW)
    with connect(database_url) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 1
        )
        receipt = connection.execute("SELECT * FROM dispatch_receipts").fetchone()
        assert receipt["account_id"] in {"CF-1001", "CF-1002", "CF-1003", "CF-1004", "CF-1005"}
    assert status(settings, NOW)["counts"]["sent"] == 1


def test_review_arriving_after_draft_blocks_scheduled_dispatch(database_url):
    from app.modules.scheduling.service import status, tick

    settings = setup_accounts(database_url)
    tick(settings, NOW)
    process_once(settings, now=NOW)
    with connect(database_url) as connection:
        message = connection.execute("SELECT * FROM collection_messages").fetchone()
        open_review(connection, message["account_id"], "hardship", NOW)
    tick(settings, NOW)
    original_reasons = status(settings, NOW)["attention_items"][0]["reason_codes"]
    tick(settings, NOW + timedelta(seconds=1))
    with connect(database_url) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 0
        )
        assert (
            connection.execute("SELECT count(*) AS n FROM collection_reviews").fetchone()["n"] == 1
        )
    assert status(settings, NOW)["counts"]["needs_attention"] == 1
    assert "case_paused" in original_reasons
    assert status(settings, NOW)["attention_items"][0]["reason_codes"] == original_reasons


def test_stale_authority_is_visible_without_fabricated_refresh(database_url):
    from app.modules.scheduling.service import status, tick

    settings = setup_accounts(database_url)
    tick(settings, NOW + timedelta(days=2))
    snapshot = status(settings, NOW + timedelta(days=2))
    assert snapshot["counts"]["needs_attention"] == 6
    assert "data_stale" in snapshot["attention_items"][0]["reason_codes"]
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 0


def test_sunday_is_deferred_without_review_or_run(database_url):
    from app.modules.scheduling.service import status, tick

    settings = setup_accounts(database_url)
    sunday = NOW + timedelta(days=1)
    with connect(database_url) as connection:
        connection.execute("UPDATE collection_authorities SET data_verified_at=%s", (sunday,))
    tick(settings, sunday)
    assert status(settings, sunday)["counts"]["deferred"] == 6  # CF-1007 is now overdue.
    with connect(database_url) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM collection_reviews").fetchone()["n"] == 0
        )
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 0


def test_scheduler_cannot_dispatch_manual_run(database_url):
    settings = setup_accounts(database_url)
    accepted = start_run(
        settings,
        "CF-1001",
        StartRunRequest(instruction="Manual reminder", idempotency_key=uuid4()),
        Caller(),
        NOW,
    )
    process_once(settings, now=NOW)
    with connect(database_url) as connection:
        message = connection.execute(
            "SELECT * FROM collection_messages WHERE run_id=%s", (accepted["run_id"],)
        ).fetchone()
    with pytest.raises(DomainError, match="dispatch"):
        simulate_dispatch(
            settings,
            message["id"],
            DispatchRequest(expected_message_version=1, idempotency_key=uuid4()),
            Caller(
                principal_id="daily-collection-scheduler",
                actor_kind="scheduler",
                account_id="CF-1001",
                run_id=accepted["run_id"],
            ),
            NOW,
        )


def test_active_manual_run_defers_until_account_is_available(database_url):
    from app.modules.scheduling.service import tick

    settings = setup_accounts(database_url)
    accepted = start_run(
        settings,
        "CF-1001",
        StartRunRequest(
            instruction="Existing manual task", task_type="collector_brief", idempotency_key=uuid4()
        ),
        Caller(),
        NOW - timedelta(minutes=1),
    )
    tick(settings, NOW)
    with connect(database_url) as connection:
        item = connection.execute(
            "SELECT * FROM collection_schedule_items WHERE account_id='CF-1001'"
        ).fetchone()
        assert item["state"] == "deferred" and item["run_id"] is None
    # Existing brief has no delivery or contact budget side effects.
    assert process_once(settings, now=NOW)
    tick(settings, NOW)
    with connect(database_url) as connection:
        item = connection.execute(
            "SELECT * FROM collection_schedule_items WHERE account_id='CF-1001'"
        ).fetchone()
        assert item["state"] == "queued" and item["run_id"] != accepted["run_id"]


def test_disabled_scheduler_admits_nothing(database_url):
    from app.modules.scheduling.service import tick

    settings = setup_accounts(database_url).model_copy(update={"scheduler_enabled": False})
    tick(settings, NOW)
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 0


def test_atomic_admission_rolls_back_run_when_linkage_fails(database_url):
    from app.modules.scheduling.service import tick

    settings = setup_accounts(database_url)
    with connect(database_url) as connection:
        connection.execute("""
            CREATE FUNCTION reject_schedule_link() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.run_id IS NOT NULL AND NEW.account_id = 'CF-1001' THEN RAISE EXCEPTION 'test link failure'; END IF;
              RETURN NEW;
            END $$;
            CREATE TRIGGER reject_schedule_link BEFORE UPDATE ON collection_schedule_items
            FOR EACH ROW EXECUTE FUNCTION reject_schedule_link();
        """)
    tick(settings, NOW)
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 4
        assert connection.execute("SELECT count(*) AS n FROM collection_jobs").fetchone()["n"] == 4
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM collection_schedule_items WHERE run_id IS NOT NULL"
            ).fetchone()["n"]
            == 4
        )

        failed = connection.execute(
            "SELECT * FROM collection_schedule_items WHERE account_id='CF-1001'"
        ).fetchone()
        assert failed["run_id"] is None and failed["state"] == "needs_attention"


def test_pre_generation_window_deferral_reuses_same_run(database_url):
    from app.modules.scheduling.service import status, tick

    settings = setup_accounts(database_url)
    with connect(database_url) as connection:
        connection.execute("UPDATE accounts SET outstanding_amount=0 WHERE id!='CF-1001'")
    tick(settings, NOW)
    # The queue may wait until the local window has closed.
    assert process_once(settings, now=NOW + timedelta(hours=12))
    tick(settings, NOW + timedelta(hours=12))
    assert status(settings, NOW)["counts"]["deferred"] == 1
    # Inject an eligible same-day clock to exercise a retry without generating another run.
    tick(settings, NOW + timedelta(minutes=1))
    assert status(settings, NOW)["counts"]["queued"] == 1
    with connect(database_url) as connection:
        assert connection.execute("SELECT count(*) AS n FROM collection_runs").fetchone()["n"] == 1


def test_paid_account_is_rechecked_before_automatic_delivery(database_url):
    from app.modules.scheduling.service import status, tick

    settings = setup_accounts(database_url)
    with connect(database_url) as connection:
        connection.execute("UPDATE accounts SET outstanding_amount=0 WHERE id!='CF-1001'")
    tick(settings, NOW)
    process_once(settings, now=NOW)
    with connect(database_url) as connection:
        connection.execute("UPDATE accounts SET outstanding_amount=0 WHERE id='CF-1001'")
    tick(settings, NOW)
    with connect(database_url) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 0
        )
    assert status(settings, NOW)["counts"]["needs_attention"] == 0
    with connect(database_url) as connection:
        item = connection.execute(
            "SELECT * FROM collection_schedule_items WHERE account_id='CF-1001'"
        ).fetchone()
        assert item["state"] == "resolved" and "account_not_collectible" in item["reason_codes"]


def test_changed_nonzero_balance_blocks_stale_scheduled_draft(database_url):
    from app.modules.scheduling.service import status, tick

    settings = setup_accounts(database_url)
    with connect(database_url) as connection:
        connection.execute("UPDATE accounts SET outstanding_amount=0 WHERE id!='CF-1001'")
    tick(settings, NOW)
    process_once(settings, now=NOW)
    with connect(database_url) as connection:
        connection.execute("UPDATE accounts SET outstanding_amount=999 WHERE id='CF-1001'")
    tick(settings, NOW)
    tick(settings, NOW + timedelta(seconds=1))
    with connect(database_url) as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM dispatch_receipts").fetchone()["n"] == 0
        )
    assert "account_facts_changed" in status(settings, NOW)["attention_items"][0]["reason_codes"]


def test_refreshed_next_day_authority_clears_historic_attention(database_url):
    from app.modules.scheduling.service import status, tick

    settings = setup_accounts(database_url)
    monday = NOW + timedelta(days=2)
    tick(settings, monday)
    assert status(settings, monday)["counts"]["needs_attention"] == 6
    tuesday = NOW + timedelta(days=3)
    with connect(database_url) as connection:
        connection.execute("UPDATE collection_authorities SET data_verified_at=%s", (tuesday,))
    tick(settings, tuesday)
    assert process_once(settings, now=tuesday)
    tick(settings, tuesday)
    snapshot = status(settings, tuesday)
    assert snapshot["counts"]["needs_attention"] == 0
    assert snapshot["counts"]["sent"] == 1
    assert snapshot["attention_items"] == []


def test_payment_resolves_runless_attention(database_url):
    from app.modules.scheduling.service import status, tick

    settings = setup_accounts(database_url)
    monday = NOW + timedelta(days=2)
    tick(settings, monday)
    with connect(database_url) as connection:
        connection.execute("UPDATE accounts SET outstanding_amount=0")
    tick(settings, monday)
    assert status(settings, monday)["counts"]["needs_attention"] == 0
    with connect(database_url) as connection:
        rows = connection.execute(
            "SELECT state,reason_codes FROM collection_schedule_items"
        ).fetchall()
        assert all(
            row["state"] == "resolved" and "data_stale" in row["reason_codes"] for row in rows
        )
