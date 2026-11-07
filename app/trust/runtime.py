"""Synthetic operational facts remain separate from reusable knowledge."""

from uuid import UUID

from app.trust.errors import TrustError
from app.trust.security import require

HOLIDAYS = [
    "2026-01-01",
    "2026-01-16",
    "2026-02-17",
    "2026-03-19",
    "2026-03-21",
    "2026-03-22",
    "2026-04-03",
    "2026-04-05",
    "2026-05-01",
    "2026-05-14",
    "2026-05-27",
    "2026-05-31",
    "2026-06-01",
    "2026-06-16",
    "2026-08-17",
    "2026-08-25",
    "2026-12-25",
    "2026-02-16",
    "2026-03-18",
    "2026-03-20",
    "2026-03-23",
    "2026-03-24",
    "2026-05-15",
    "2026-05-28",
    "2026-12-24",
    "2027-01-01",
    "2027-01-05",
    "2027-02-06",
    "2027-03-08",
    "2027-03-10",
    "2027-03-11",
    "2027-03-26",
    "2027-03-28",
    "2027-05-01",
    "2027-05-06",
    "2027-05-17",
    "2027-05-20",
    "2027-06-01",
    "2027-06-06",
    "2027-08-15",
    "2027-08-17",
    "2027-12-25",
    "2027-12-26",
    "2027-02-05",
    "2027-03-09",
    "2027-03-12",
    "2027-03-15",
    "2027-03-25",
    "2027-05-18",
    "2027-05-19",
    "2027-12-24",
]


def scoped_case(conn, actor, case_id, *, lock=False):
    try:
        value = UUID(str(case_id))
    except ValueError as exc:
        raise TrustError("INVALID_CASE_ID", 422) from exc
    row = conn.execute(
        "SELECT * FROM trust.cases WHERE id=%s AND principal=%s AND product=%s"
        + (" FOR UPDATE" if lock else ""),
        (value, actor.principal, actor.product),
    ).fetchone()
    if row is None:
        raise TrustError("CASE_NOT_FOUND", 404)
    return row


def create_case(conn, actor, data, now):
    require(actor, "case:create")
    # The UUID case API cannot infer borrower/account authority. Mapping and
    # initial canonical account provisioning are explicit administrative work.
    raise TrustError("legacy_case_mapping_required", 409)


def contact_context(conn, actor, case):
    history = conn.execute(
        """SELECT delivered_at FROM trust.dispatches
        WHERE principal=%s AND product=%s AND borrower_ref=%s AND purpose='proactive'""",
        (actor.principal, actor.product, case["borrower_ref"]),
    ).fetchall()
    return {
        **case["context"],
        "proactive_history": [r["delivered_at"].isoformat() for r in history],
    }


def receive_conversation(conn, actor, case_id, text, now):
    from app.modules.conversations.compatibility import receive_legacy

    return receive_legacy(conn, actor, case_id, text, now)


def read_conversations(conn, actor, case_id):
    from app.modules.conversations.compatibility import read_legacy

    return read_legacy(conn, actor, case_id)
