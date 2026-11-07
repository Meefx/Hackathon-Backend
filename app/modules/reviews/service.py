from typing import Any
from uuid import uuid4

from psycopg import Connection

from app.db.connection import connect
from app.modules.audit.service import append_event
from app.modules.cases.repository import contact_state, lock_case
from app.modules.compliance.service import evaluate_contact_policy
from app.shared.errors import DomainError
from app.shared.idempotency import record, replay


def get_account_review(
    connection: Connection[dict[str, Any]], account_id: str, review_id: str
) -> dict[str, Any]:
    review = connection.execute(
        "SELECT * FROM collection_reviews WHERE id=%s AND account_id=%s", (review_id, account_id)
    ).fetchone()
    if not review:
        raise DomainError("review_not_found", "Account review not found", 404)
    return review


def open_review(connection, account_id, reason, now, run_id=None, owner_id="local-operator"):
    lock_case(connection, account_id)
    review_id = str(uuid4())
    connection.execute(
        "INSERT INTO collection_reviews(id,account_id,run_id,reason,created_at) "
        "VALUES (%s,%s,%s,%s,%s)",
        (review_id, account_id, run_id, reason, now),
    )
    connection.execute(
        "INSERT INTO collection_holds(id,account_id,review_id,kind,owner_id,created_at) "
        "VALUES (%s,%s,%s,'review',%s,%s)",
        (str(uuid4()), account_id, review_id, owner_id, now),
    )
    connection.execute(
        "UPDATE collection_cases SET version=version+1,updated_at=%s WHERE account_id=%s",
        (now, account_id),
    )
    connection.execute(
        "UPDATE collection_runs SET cancellation_requested_at=COALESCE("
        "cancellation_requested_at,%s),version=version+1 WHERE account_id=%s AND status='running'",
        (now, account_id),
    )
    append_event(
        connection, account_id, "review_opened", "backend", "paused", now, run_id, [reason]
    )
    return review_id


def case_result(connection, account_id, applied, reasons=None, review_id=None):
    case = lock_case(connection, account_id)
    return {
        "account_id": account_id,
        "case_version": case["version"],
        "contact_state": contact_state(connection, case),
        "action_applied": applied,
        "reason_codes": reasons or [],
        "review_id": review_id,
    }


def resume_in_transaction(connection, account_id, now, caller, review_id=None):
    case = lock_case(connection, account_id)
    if contact_state(connection, case) == "paused_human_review":
        return case_result(connection, account_id, False, ["open_review"])
    decision = evaluate_contact_policy(connection, account_id, now)
    reasons = [r for r in decision["reason_codes"] if r != "case_paused"]
    if reasons:
        return case_result(connection, account_id, False, reasons)
    if review_id:
        # A review resolution owns only that review's hold, never another operator's decision.
        connection.execute(
            "UPDATE collection_holds SET released_at=%s WHERE account_id=%s "
            "AND review_id=%s AND released_at IS NULL",
            (now, account_id, review_id),
        )
    else:
        connection.execute(
            "UPDATE collection_holds SET released_at=%s WHERE account_id=%s "
            "AND owner_id=%s AND released_at IS NULL",
            (now, account_id, caller.principal_id),
        )
    connection.execute(
        "UPDATE collection_cases SET operator_hold=EXISTS(SELECT 1 FROM "
        "collection_holds WHERE account_id=%s AND released_at IS NULL),version=version+1,"
        "updated_at=%s WHERE account_id=%s",
        (account_id, now, account_id),
    )
    result = case_result(connection, account_id, True)
    if result["contact_state"] != "active":
        result["action_applied"] = False
        result["reason_codes"] = ["other_hold"]
    return result


def case_action(settings, account_id, request, caller, now, takeover=False):
    with connect(settings.database_url) as connection:
        case = lock_case(connection, account_id)
        scope = ("takeover:" if takeover else "resume:") + account_id
        payload = request.model_dump(mode="json")
        if old := replay(connection, scope, str(request.idempotency_key), payload):
            return old
        if case["version"] != request.expected_case_version:
            raise DomainError("version_conflict", "Case version has changed")
        if takeover:
            review_id = open_review(
                connection, account_id, "operator_takeover", now, owner_id=caller.principal_id
            )
            result = case_result(connection, account_id, True, review_id=review_id)
        else:
            result = resume_in_transaction(connection, account_id, now, caller)
        append_event(
            connection,
            account_id,
            "case_takeover" if takeover else "case_resume",
            caller.actor_kind,
            "applied" if result["action_applied"] else "blocked",
            now,
            reason_codes=result["reason_codes"],
        )
        record(connection, scope, str(request.idempotency_key), payload, result)
        return result


def resolve_review(settings, review_id, request, caller, now):
    with connect(settings.database_url) as connection:
        review = connection.execute(
            "SELECT * FROM collection_reviews WHERE id=%s", (review_id,)
        ).fetchone()
        if not review:
            raise DomainError("not_found", "Review not found", 404)
        lock_case(connection, review["account_id"])
        review = connection.execute(
            "SELECT * FROM collection_reviews WHERE id=%s FOR UPDATE", (review_id,)
        ).fetchone()
        scope = "resolve:" + review_id
        payload = request.model_dump(mode="json")
        if old := replay(connection, scope, str(request.idempotency_key), payload):
            return old
        if review["version"] != request.expected_version or review["status"] != "open":
            raise DomainError("version_conflict", "Review version has changed")
        review = connection.execute(
            "UPDATE collection_reviews SET status='resolved',version=version+1,"
            "resolved_at=%s,resolution_note=%s WHERE id=%s RETURNING *",
            (now, request.note, review_id),
        ).fetchone()
        connection.execute(
            "UPDATE collection_holds SET kind='operator',owner_id=%s "
            "WHERE review_id=%s AND released_at IS NULL",
            (caller.principal_id, review_id),
        )
        connection.execute(
            "UPDATE collection_cases SET operator_hold=TRUE,version=version+1,"
            "updated_at=%s WHERE account_id=%s",
            (now, review["account_id"]),
        )
        result_case = (
            resume_in_transaction(connection, review["account_id"], now, caller, review_id)
            if request.decision == "request_resume"
            else case_result(connection, review["account_id"], True)
        )
        from app.modules.reviews.schemas import ReviewItem

        result = {
            "review": ReviewItem.model_validate(review).model_dump(mode="json"),
            "case": result_case,
        }
        append_event(
            connection,
            review["account_id"],
            "review_resolved",
            caller.actor_kind,
            request.decision,
            now,
            review["run_id"],
        )
        record(connection, scope, str(request.idempotency_key), payload, result)
        return result
