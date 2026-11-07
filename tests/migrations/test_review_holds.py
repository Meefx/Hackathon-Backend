from datetime import UTC, datetime
from uuid import uuid4

from alembic import command

from app.config import get_settings
from app.db.connection import REQUIRED_REVISION, connect
from app.modules.reviews.schemas import ReviewResolution
from app.modules.reviews.service import resolve_review
from app.seed import seed
from app.shared.caller import Caller
from tests.conftest import migration_config


def test_0003_preserves_portfolio_and_retains_legacy_holds(database_url):
    seed()
    command.downgrade(migration_config(), "0002_collection_state")
    review_id = str(uuid4())
    now = datetime(2026, 11, 7, 3, tzinfo=UTC)
    with connect(database_url) as connection:
        accounts = connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        connection.execute(
            "UPDATE collection_cases SET operator_hold=TRUE WHERE account_id='CF-1001'"
        )
        connection.execute(
            "INSERT INTO collection_reviews(id,account_id,reason,created_at) "
            "VALUES (%s,'CF-1001','human_request',%s)",
            (review_id, now),
        )
    command.upgrade(migration_config(), "head")
    with connect(database_url) as connection:
        assert connection.execute("SELECT * FROM accounts ORDER BY id").fetchall() == accounts
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"]
            == REQUIRED_REVISION
        )
        holds = connection.execute(
            "SELECT * FROM collection_holds WHERE account_id='CF-1001'"
        ).fetchall()
        assert {hold["kind"] for hold in holds} == {"legacy", "review"}
    result = resolve_review(
        get_settings(),
        review_id,
        ReviewResolution(
            decision="request_resume",
            expected_version=1,
            note="Review resolution must preserve legacy hold",
            idempotency_key=uuid4(),
        ),
        Caller(),
        now,
    )
    assert result["case"]["contact_state"] == "paused_operator"
