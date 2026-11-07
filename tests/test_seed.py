from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.seed as seed_module
from app.db.connection import connect

TODAY = date(2026, 11, 7)


@pytest.mark.parametrize("count", [0, -1])
def test_seed_rejects_invalid_count_before_database_access(monkeypatch, count):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("Invalid seed count must be rejected before connecting to PostgreSQL")

    monkeypatch.setattr(seed_module, "connect", unexpected_connection)
    with pytest.raises(ValueError):
        seed_module.seed(TODAY, count=count)


def test_expanding_seed_preserves_existing_accounts_and_collection_state(database_url):
    assert seed_module.seed(TODAY) == 8
    with connect(database_url) as connection:
        connection.execute(
            "UPDATE accounts SET outstanding_amount=123456, assigned_to='Existing owner' "
            "WHERE id='CF-1001'"
        )
        connection.execute(
            "UPDATE collection_cases SET operator_hold=TRUE, eligible=FALSE, "
            "recipient_verified=FALSE WHERE account_id='CF-1001'"
        )
        connection.execute(
            "UPDATE collection_authorities SET opted_out=TRUE, verified=FALSE, "
            "mandate_active=FALSE WHERE account_id='CF-1001'"
        )
        before = connection.execute(
            "SELECT a.*, to_jsonb(c) AS case_state, to_jsonb(u) AS authority "
            "FROM accounts a JOIN collection_cases c ON c.account_id=a.id "
            "JOIN collection_authorities u ON u.account_id=a.id ORDER BY a.id"
        ).fetchall()

    assert seed_module.seed(TODAY + timedelta(days=10), count=200) == 192
    assert seed_module.seed(TODAY + timedelta(days=20), count=200) == 0
    with connect(database_url) as connection:
        after = connection.execute(
            "SELECT a.*, to_jsonb(c) AS case_state, to_jsonb(u) AS authority "
            "FROM accounts a JOIN collection_cases c ON c.account_id=a.id "
            "JOIN collection_authorities u ON u.account_id=a.id "
            "WHERE a.id <= 'CF-1008' ORDER BY a.id"
        ).fetchall()
        assert connection.execute("SELECT count(*) AS n FROM accounts").fetchone()["n"] == 200
    assert after == before


def test_large_seed_is_concurrent_idempotent_diverse_and_fully_provisioned(database_url):
    count = 1000
    with ThreadPoolExecutor(max_workers=2) as executor:
        inserted = list(executor.map(lambda _: seed_module.seed(TODAY, count=count), range(2)))
    assert sum(inserted) == count
    with connect(database_url) as connection:
        rows = connection.execute(
            "SELECT a.*, c.recipient_id, c.recipient_verified, c.eligible, "
            "u.principal, u.product, u.borrower_ref, u.borrower_timezone, "
            "u.borrower_owned, u.verified, u.opted_out, u.is_synthetic, "
            "u.data_verified_at, u.mandate_active, u.calendar_years, u.holidays "
            "FROM accounts a JOIN collection_cases c ON c.account_id=a.id "
            "JOIN collection_authorities u ON u.account_id=a.id ORDER BY a.id"
        ).fetchall()
    assert len(rows) == count
    assert {row["id"] for row in rows} == {f"CF-{1001 + index}" for index in range(count)}
    assert len({row["loan_reference"] for row in rows}) == count
    assert len({row["borrower_name"] for row in rows}) == count
    assert len({row["borrower_ref"] for row in rows}) == count
    assert len({row["assigned_to"] for row in rows}) > 2
    assert len({row["outstanding_amount"] for row in rows}) > 8
    days = [(TODAY - row["due_date"]).days for row in rows]
    assert any(value <= 0 for value in days)
    assert any(1 <= value <= 30 for value in days)
    assert any(31 <= value <= 90 for value in days)
    assert any(value > 90 for value in days)
    for row in rows:
        assert isinstance(row["outstanding_amount"], int) and row["outstanding_amount"] > 0
        assert row["recipient_id"] == f"synthetic:{row['id']}"
        assert row["recipient_verified"] and row["eligible"]
        assert row["principal"] == "cf" and row["product"] == "spaylater"
        assert isinstance(row["borrower_ref"], UUID)
        assert row["borrower_timezone"] == "Asia/Jakarta"
        assert row["borrower_owned"] and row["verified"] and row["is_synthetic"]
        assert not row["opted_out"] and row["mandate_active"]
        assert row["data_verified_at"].astimezone(ZoneInfo("Asia/Jakarta")).date() == TODAY
        assert TODAY.year in row["calendar_years"]
        assert row["holidays"] == []

    assert seed_module.seed(TODAY + timedelta(days=10), count=count) == 0
    with connect(database_url) as connection:
        after = connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    assert after == [{key: row[key] for key in after[0]} for row in rows]


def test_expanded_portfolio_http_pagination_search_filters_and_dashboard(database_url, monkeypatch):
    count = 200
    seed_module.seed(TODAY, count=count)
    clock = Mock(wraps=datetime)
    clock.now.return_value = datetime(2026, 11, 7, 3, tzinfo=UTC)
    monkeypatch.setattr(main_module, "datetime", clock)
    with connect(database_url) as connection:
        expected = connection.execute("SELECT * FROM accounts ORDER BY due_date, id").fetchall()
    with TestClient(main_module.create_app()) as client:
        items = []
        for offset in range(0, count, 100):
            response = client.get("/api/v1/accounts", params={"limit": 100, "offset": offset})
            assert response.status_code == 200
            page = response.json()
            assert (page["total"], page["limit"], page["offset"]) == (count, 100, offset)
            items.extend(page["items"])
        assert [item["id"] for item in items] == [row["id"] for row in expected]
        assert len({item["id"] for item in items}) == count
        assert client.get("/api/v1/accounts?limit=20&offset=190").json()["items"] == items[190:]
        assert client.get("/api/v1/accounts?offset=200").json()["items"] == []

        target = expected[-1]
        search = client.get(
            "/api/v1/accounts", params={"search": f" {target['loan_reference'].lower()} "}
        ).json()
        assert search["total"] == 1
        assert search["items"][0]["id"] == target["id"]
        name = target["borrower_name"].casefold()
        matching = [row for row in expected if name in row["borrower_name"].casefold()]
        assert client.get("/api/v1/accounts", params={"search": name}).json()["total"] == len(
            matching
        )

        ranges = [("current", None, 0), ("early", 1, 30), ("late", 31, 90), ("critical", 91, None)]
        buckets = []
        for status, minimum, maximum in ranges:
            matching = [
                row
                for row in expected
                if (minimum is None or (TODAY - row["due_date"]).days >= minimum)
                and (maximum is None or (TODAY - row["due_date"]).days <= maximum)
            ]
            filtered = client.get("/api/v1/accounts", params={"status": status, "limit": 10}).json()
            assert filtered["total"] == len(matching)
            assert [item["id"] for item in filtered["items"]] == [
                row["id"] for row in matching[:10]
            ]
            buckets.append(
                {
                    "status": status,
                    "count": len(matching),
                    "outstanding_amount": sum(row["outstanding_amount"] for row in matching),
                }
            )
        overdue = [row for row in expected if row["due_date"] < TODAY]
        assert client.get("/api/v1/dashboard").json() == {
            "total_accounts": count,
            "total_outstanding": sum(row["outstanding_amount"] for row in expected),
            "overdue_accounts": len(overdue),
            "overdue_amount": sum(row["outstanding_amount"] for row in overdue),
            "buckets": buckets,
        }
