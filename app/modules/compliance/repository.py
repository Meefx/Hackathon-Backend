"""Persistence for contact authority, borrower serialization and preserved history."""


def authority(connection, account_id: str, *, lock: bool = False) -> dict | None:
    return connection.execute(
        "SELECT * FROM collection_authorities WHERE account_id=%s"
        + (" FOR UPDATE" if lock else ""),
        (account_id,),
    ).fetchone()


def lock_contact_authority(connection, account_id: str) -> dict | None:
    current = authority(connection, account_id, lock=True)
    connection.execute("SELECT version FROM collection_policies WHERE active FOR SHARE").fetchall()
    if current:
        budget = "collection-borrower:" + ":".join(
            str(current[key]) for key in ("principal", "product", "borrower_ref")
        )
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (budget,))
    return current


def contact_history(connection, current: dict) -> list:
    scope = (current["principal"], current["product"], current["borrower_ref"])
    # Trust dispatches are preserved read-only at cutover. They continue consuming
    # the same borrower budget, even when several accounts map to that borrower.
    rows = connection.execute(
        "SELECT r.created_at AS contacted_at FROM dispatch_receipts r "
        "JOIN collection_authorities a ON a.account_id=r.account_id "
        "WHERE a.principal=%s AND a.product=%s AND a.borrower_ref=%s "
        "UNION ALL SELECT delivered_at AS contacted_at FROM trust.dispatches "
        "WHERE principal=%s AND product=%s AND borrower_ref=%s AND purpose='proactive'",
        scope + scope,
    ).fetchall()
    return [row["contacted_at"] for row in rows]


def legacy_holds(connection, account_id: str) -> bool:
    rows = connection.execute(
        "SELECT c.context FROM trust.cases c JOIN legacy_case_account_mappings m "
        "ON m.case_id=c.id WHERE m.account_id=%s",
        (account_id,),
    ).fetchall()
    return any(
        row["context"].get("state") not in {"active", "in_conversation", "promise_to_pay"}
        or row["context"].get("opted_out") is not False
        or row["context"].get("verified") is not True
        or row["context"].get("timezone") not in {"Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura"}
        for row in rows
    )
