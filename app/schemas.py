from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

CollectionStatus = Literal["current", "early", "late", "critical"]


class Account(BaseModel):
    id: str
    borrower_name: str
    loan_reference: str
    outstanding_amount: int = Field(ge=0, description="Outstanding balance in whole IDR")
    due_date: date
    assigned_to: str
    days_past_due: int = Field(ge=0)
    status: CollectionStatus


class AccountPage(BaseModel):
    items: list[Account]
    total: int
    limit: int
    offset: int


class Bucket(BaseModel):
    status: CollectionStatus
    count: int
    outstanding_amount: int


class Dashboard(BaseModel):
    total_accounts: int
    total_outstanding: int
    overdue_accounts: int
    overdue_amount: int
    buckets: list[Bucket]


class Health(BaseModel):
    status: Literal["ok"]
    service: str
