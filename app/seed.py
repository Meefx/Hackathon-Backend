"""Explicit, transactional fictional demo seed. Run migrations first."""

from argparse import ArgumentParser
from datetime import date, datetime, time, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from app.config import get_settings
from app.db.connection import connect
from app.demo_accounts import demo_accounts


def seed(today: date | None = None, *, count: int = 8) -> int:
    samples = demo_accounts(count)
    today = today or datetime.now(ZoneInfo("Asia/Jakarta")).date()
    with connect(get_settings().database_url) as connection, connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO accounts "
            "(id, borrower_name, loan_reference, outstanding_amount, due_date, assigned_to) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [
                (ref, name, f"LN-{ref[3:]}", amount, today - timedelta(days=days), owner)
                for ref, name, amount, days, owner in samples
            ],
        )
        inserted = cursor.rowcount
        connection.execute(
            "INSERT INTO collection_policies VALUES "
            "('demo-policy-v1',TRUE,8,20,'2027-12-31',1,'demo_fixture') ON CONFLICT DO NOTHING"
        )
        cursor.executemany(
            "INSERT INTO collection_cases(account_id,recipient_id,"
            "recipient_verified,eligible) VALUES (%s,%s,TRUE,TRUE) ON CONFLICT DO NOTHING",
            [(ref, "synthetic:" + ref) for ref, *_ in samples],
        )
        cursor.executemany(
            "INSERT INTO collection_authorities(account_id,principal,product,borrower_ref,"
            "borrower_timezone,borrower_owned,verified,opted_out,is_synthetic,"
            "data_verified_at,mandate_active,calendar_years,holidays) "
            "VALUES (%s,'cf','spaylater',%s,'Asia/Jakarta',TRUE,TRUE,FALSE,TRUE,"
            "%s,TRUE,%s,%s) ON CONFLICT DO NOTHING",
            [
                (
                    ref,
                    uuid5(NAMESPACE_URL, "collectflow:synthetic:" + ref),
                    datetime.combine(today, time.min, ZoneInfo("Asia/Jakarta")),
                    Jsonb([2026, 2027]),
                    Jsonb([]),
                )
                for ref, *_ in samples
            ],
        )
    return inserted


if __name__ == "__main__":
    parser = ArgumentParser(description="Seed a fictional CollectFlow demo portfolio.")
    parser.add_argument("--count", type=int, default=1000, help="Demo portfolio size (default: 1000)")
    arguments = parser.parse_args()
    if arguments.count < 1:
        parser.error("--count must be positive")
    print(f"Inserted {seed(count=arguments.count)} fictional demo accounts.")
