"""Synthetic DB inbox. No external delivery or network adapters exist."""

from uuid import uuid4


def deliver(conn, actor, attempt, case, text, now):
    dispatch_id = uuid4()
    conn.execute(
        """INSERT INTO trust.dispatches
        (id,attempt_id,principal,product,case_id,borrower_ref,text_sha256,body,purpose,
         is_simulated,delivered_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s)""",
        (
            dispatch_id,
            attempt["id"],
            actor.principal,
            actor.product,
            case["id"],
            case["borrower_ref"],
            attempt["text_sha256"],
            text,
            case["context"]["purpose"],
            now,
        ),
    )
    return str(dispatch_id)
