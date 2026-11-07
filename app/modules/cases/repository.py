from app.shared.errors import DomainError


def lock_case(connection, account_id):
    account = connection.execute(
        "SELECT * FROM accounts WHERE id=%s FOR UPDATE", (account_id,)
    ).fetchone()
    if not account:
        raise DomainError("not_found", "Account not found", 404)
    connection.execute(
        "INSERT INTO collection_cases(account_id) VALUES (%s) ON CONFLICT DO NOTHING", (account_id,)
    )
    return connection.execute(
        "SELECT * FROM collection_cases WHERE account_id=%s FOR UPDATE", (account_id,)
    ).fetchone()


def contact_state(connection, case):
    review = connection.execute(
        "SELECT id FROM collection_reviews WHERE account_id=%s AND status='open' LIMIT 1",
        (case["account_id"],),
    ).fetchone()
    hold = connection.execute(
        "SELECT id FROM collection_holds WHERE account_id=%s AND released_at IS NULL LIMIT 1",
        (case["account_id"],),
    ).fetchone()
    return "paused_human_review" if review else "paused_operator" if hold else "active"
