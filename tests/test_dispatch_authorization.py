"""Direct service entry cannot bypass the HTTP dispatch capability boundary."""

import pytest

from app.core.clock import FrozenClock
from app.services.dispatch_gate import dispatch
from app.trust.errors import TrustError
from tests.test_trust_pipeline import APPROVAL_SECRET, prepared, scenario


@pytest.mark.parametrize("role", ["knowledge", "qa", "personal", "manager", "admin"])
@pytest.mark.parametrize("already_delivered", [False, True])
def test_dispatch_checks_capability_before_delivery_or_replay(
    trust_conn, trust_actor, trust_now, role, already_delivered
):
    _, template, case = scenario(trust_conn, trust_actor, trust_now)
    draft = prepared(trust_conn, trust_actor, trust_now, template, case)
    if already_delivered:
        result = dispatch(
            trust_conn, trust_actor("operator"), draft, APPROVAL_SECRET, FrozenClock(trust_now)
        )
        assert result["status"] == "DELIVERED"

    with pytest.raises(TrustError, match="CAPABILITY_DENIED") as failure:
        dispatch(trust_conn, trust_actor(role), draft, APPROVAL_SECRET, FrozenClock(trust_now))
    assert failure.value.status == 403
    row = trust_conn.execute(
        "SELECT count(*) AS n FROM trust.dispatches WHERE attempt_id=%s", (draft["attempt_id"],)
    ).fetchone()
    assert row["n"] == int(already_delivered)
    attempt = trust_conn.execute(
        "SELECT status FROM trust.attempts WHERE id=%s", (draft["attempt_id"],)
    ).fetchone()
    assert attempt["status"] == ("DELIVERED" if already_delivered else "APPROVED")
