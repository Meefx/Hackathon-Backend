"""Integration fixtures use an explicit disposable PostgreSQL database."""

import os
from datetime import UTC, datetime

import pytest


@pytest.fixture
def trust_now():
    return datetime(2026, 11, 9, 3, tzinfo=UTC)


@pytest.fixture
def trust_actor():
    from app.trust.security import Actor

    def actor(role="knowledge", principal="cf", product="spaylater"):
        return Actor(subject=f"demo-{role}", role=role, principal=principal, product=product)

    return actor


@pytest.fixture
def trust_dsn():
    dsn = os.environ.get("TRUST_TEST_DATABASE_URL")
    if not dsn:
        pytest.fail("Set TRUST_TEST_DATABASE_URL to an isolated PostgreSQL17 database")
    return dsn


@pytest.fixture
def trust_conn(trust_dsn):
    from app.trust.db import connection, initialize

    initialize(trust_dsn)
    with connection(trust_dsn) as conn:
        yield conn
        conn.rollback()
