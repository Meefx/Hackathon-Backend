"""Portfolio aging uses calendar dates supplied by the caller."""

from datetime import date

from app.modules.accounts import repository


def serialize_account(row: dict, today: date) -> dict:
    days = max(0, (today - row["due_date"]).days)
    status = "current" if days == 0 else "early" if days <= 30 else "late" if days <= 90 else "critical"
    return {**row, "days_past_due": days, "status": status}


def list_accounts(database_url: str, today: date) -> list[dict]:
    return [serialize_account(row, today) for row in repository.list_accounts(database_url)]


def get_account(database_url: str, account_id: str, today: date) -> dict | None:
    row = repository.get_account(database_url, account_id)
    return serialize_account(row, today) if row is not None else None
