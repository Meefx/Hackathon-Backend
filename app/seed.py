"""Explicit, transactional fictional demo seed. Run migrations first."""

from datetime import date, datetime, time, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from app.config import get_settings
from app.db.connection import connect


def seed(today: date | None = None) -> int:
    today = today or datetime.now(ZoneInfo("Asia/Jakarta")).date()
    samples = [
        ("CF-1001", "Alex Morgan", 4_500_000, 104, "Sarah Wilson"),
        ("CF-1002", "Jordan Lee", 2_750_000, 67, "Daniel Brooks"),
        ("CF-1003", "Taylor Reed", 1_200_000, 42, "Sarah Wilson"),
        ("CF-1004", "Casey Parker", 3_600_000, 24, "Daniel Brooks"),
        ("CF-1005", "Riley Quinn", 850_000, 12, "Sarah Wilson"),
        ("CF-1006", "Jamie Blake", 5_000_000, 5, "Daniel Brooks"),
        ("CF-1007", "Avery Stone", 2_100_000, 0, "Sarah Wilson"),
        ("CF-1008", "Morgan Ellis", 1_500_000, -7, "Daniel Brooks"),
    ]
    inserted = 0
    with connect(get_settings().database_url) as connection:
        for ref, name, amount, days, owner in samples:
            cursor = connection.execute(
                "INSERT INTO accounts "
                "(id, borrower_name, loan_reference, outstanding_amount, due_date, assigned_to) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (ref, name, f"LN-{ref[3:]}", amount, today - timedelta(days=days), owner),
            )
            inserted += cursor.rowcount
        connection.execute(
            "INSERT INTO collection_policies VALUES "
            "('demo-policy-v1',TRUE,8,20,'2027-12-31',1,'demo_fixture') ON CONFLICT DO NOTHING"
        )
        for ref, *_ in samples:
            connection.execute(
                "INSERT INTO collection_cases(account_id,recipient_id,"
                "recipient_verified,eligible) VALUES (%s,%s,TRUE,TRUE) ON CONFLICT DO NOTHING",
                (ref, "synthetic:" + ref),
            )
            connection.execute(
                "INSERT INTO collection_authorities(account_id,principal,product,borrower_ref,"
                "borrower_timezone,borrower_owned,verified,opted_out,is_synthetic,"
                "data_verified_at,mandate_active,calendar_years,holidays) "
                "VALUES (%s,'cf','spaylater',%s,'Asia/Jakarta',TRUE,TRUE,FALSE,TRUE,"
                "%s,TRUE,%s,%s) ON CONFLICT DO NOTHING",
                (
                    ref,
                    uuid5(NAMESPACE_URL, "collectflow:synthetic:" + ref),
                    datetime.combine(today, time.min, ZoneInfo("Asia/Jakarta")),
                    Jsonb([2026, 2027]),
                    Jsonb([]),
                ),
            )
    return inserted


if __name__ == "__main__":
    print(f"Inserted {seed()} fictional demo accounts.")
