"""Explicit synthetic fixtures for the standalone compatibility demonstration."""

from datetime import datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.db.connection import connect


def provision_synthetic_case(database_url: str, now: datetime) -> dict:
    """Seed a fresh fictional account and its reviewed legacy mapping explicitly."""
    account_id = "DEMO-" + uuid4().hex
    case_id, borrower_ref = uuid4(), uuid4()
    with connect(database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(71107002)")
        connection.execute(
            "INSERT INTO accounts VALUES (%s,%s,%s,%s,%s,%s)",
            (
                account_id,
                "Demo Borrower",
                "LN-" + account_id,
                1_250_000,
                now.date(),
                "Demo Officer",
            ),
        )
        connection.execute(
            "INSERT INTO collection_policies "
            "SELECT 'demo-policy-v1',TRUE,8,20,'2027-12-31',1,'demo_fixture' "
            "WHERE NOT EXISTS(SELECT 1 FROM collection_policies WHERE active) "
            "ON CONFLICT DO NOTHING"
        )
        connection.execute(
            "INSERT INTO collection_cases(account_id,recipient_id,recipient_verified,eligible) "
            "VALUES (%s,%s,TRUE,TRUE)",
            (account_id, "synthetic:" + account_id),
        )
        connection.execute(
            "INSERT INTO collection_authorities(account_id,principal,product,borrower_ref,"
            "borrower_timezone,borrower_owned,verified,opted_out,is_synthetic,"
            "data_verified_at,mandate_active,calendar_years,holidays) "
            "VALUES (%s,'cf','spaylater',%s,'Asia/Jakarta',TRUE,TRUE,FALSE,TRUE,"
            "%s,TRUE,%s,%s)",
            (account_id, borrower_ref, now, Jsonb([2026, 2027]), Jsonb([])),
        )
        connection.execute(
            "INSERT INTO trust.cases(id,principal,product,borrower_ref,is_synthetic,"
            "context,facts,created_at) VALUES (%s,'cf','spaylater',%s,TRUE,%s,%s,%s)",
            (
                case_id,
                borrower_ref,
                Jsonb(
                    {
                        "state": "active",
                        "opted_out": False,
                        "verified": True,
                        "timezone": "Asia/Jakarta",
                        "borrower_owned": True,
                    }
                ),
                Jsonb({"borrower_first_name": "Demo Borrower"}),
                now,
            ),
        )
        connection.execute(
            "INSERT INTO legacy_case_account_mappings(case_id,account_id) VALUES (%s,%s)",
            (case_id, account_id),
        )
    return {"id": str(case_id), "account_id": account_id, "is_synthetic": True}
