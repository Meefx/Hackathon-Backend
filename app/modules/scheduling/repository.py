"""Persistence owned by the daily scheduling domain."""

from psycopg.types.json import Jsonb


def candidates(connection, today):
    return connection.execute(
        "SELECT id FROM accounts WHERE due_date<%s AND outstanding_amount>0 ORDER BY due_date,id",
        (today,),
    ).fetchall()


def update_item(connection, item_id, state, reasons, now):
    connection.execute(
        "UPDATE collection_schedule_items SET state=%s,reason_codes=%s,updated_at=%s WHERE id=%s "
        "AND (state!=%s OR reason_codes!=%s)",
        (
            state,
            Jsonb(list(dict.fromkeys(reasons))),
            now,
            item_id,
            state,
            Jsonb(list(dict.fromkeys(reasons))),
        ),
    )


def pending_deliveries(connection):
    return connection.execute(
        "SELECT s.*,r.status AS run_status,r.outcome,r.reason_codes AS run_reasons "
        "FROM collection_schedule_items s JOIN collection_runs r ON r.id=s.run_id "
        "WHERE s.state NOT IN ('sent','resolved') ORDER BY s.created_at,s.id"
    ).fetchall()


def outbound_message(connection, run_id):
    return connection.execute(
        "SELECT * FROM collection_messages WHERE run_id=%s AND direction='outbound' "
        "ORDER BY created_at,id LIMIT 1",
        (run_id,),
    ).fetchone()


def receipt_for_message(connection, message_id):
    return connection.execute(
        "SELECT id FROM dispatch_receipts WHERE message_id=%s",
        (message_id,),
    ).fetchone()


def attention_candidates(connection):
    return connection.execute(
        "SELECT s.*,a.outstanding_amount,a.due_date,r.status AS run_status "
        "FROM collection_schedule_items s JOIN accounts a ON a.id=s.account_id "
        "LEFT JOIN collection_runs r ON r.id=s.run_id WHERE s.state='needs_attention'"
    ).fetchall()


def later_receipt(connection, account_id, created_at):
    return connection.execute(
        "SELECT id FROM dispatch_receipts WHERE account_id=%s AND created_at>%s LIMIT 1",
        (account_id, created_at),
    ).fetchone()


def run_has_effects(connection, run_id):
    return connection.execute(
        "SELECT EXISTS(SELECT 1 FROM collection_messages WHERE run_id=%s) OR "
        "EXISTS(SELECT 1 FROM run_artifacts WHERE run_id=%s) OR "
        "EXISTS(SELECT 1 FROM knowledge_snapshots WHERE run_id=%s) OR "
        "EXISTS(SELECT 1 FROM collection_runs WHERE id=%s AND runtime_snapshot IS NOT NULL) AS present",
        (run_id, run_id, run_id, run_id),
    ).fetchone()["present"]


def active_run(connection, account_id, exclude_run_id=""):
    return connection.execute(
        "SELECT status FROM collection_runs WHERE account_id=%s AND id!=%s "
        "AND status IN ('queued','running','interrupted') LIMIT 1",
        (account_id, exclude_run_id),
    ).fetchone()


def requeue_run(connection, run_id):
    return connection.execute(
        "UPDATE collection_runs SET status='queued',phase='queued',finished_at=NULL,"
        "reason_codes='[]' WHERE id=%s AND cancellation_requested_at IS NULL",
        (run_id,),
    ).rowcount


def ensure_item(connection, item_id, today, account_id, now):
    connection.execute(
        "INSERT INTO collection_schedule_items(id,business_date,account_id,state,created_at,updated_at) "
        "VALUES (%s,%s,%s,'deferred',%s,%s) ON CONFLICT(business_date,account_id) DO NOTHING",
        (item_id, today, account_id, now, now),
    )
    return connection.execute(
        "SELECT * FROM collection_schedule_items WHERE id=%s FOR UPDATE",
        (item_id,),
    ).fetchone()


def manager_session(connection, account_id, principal):
    return connection.execute(
        "SELECT id FROM manager_sessions WHERE account_id=%s AND caller_id=%s "
        "ORDER BY created_at,id LIMIT 1",
        (account_id, principal),
    ).fetchone()


def account_facts(connection, account_id):
    return connection.execute(
        "SELECT outstanding_amount,due_date,borrower_name,loan_reference FROM accounts WHERE id=%s",
        (account_id,),
    ).fetchone()


def link_run(connection, item_id, run_id, facts_hash, now):
    connection.execute(
        "UPDATE collection_schedule_items SET run_id=%s,state='queued',reason_codes='[]',"
        "updated_at=%s,facts_hash=%s WHERE id=%s",
        (run_id, now, facts_hash, item_id),
    )


def lock_scheduler(connection):
    connection.execute("SELECT pg_advisory_xact_lock(71107006)")
    connection.execute("SELECT pg_advisory_xact_lock(71107002)")


def record_scan(connection, now):
    connection.execute(
        "INSERT INTO collection_schedule_state(id,last_scan_at) VALUES (1,%s) "
        "ON CONFLICT(id) DO UPDATE SET last_scan_at=EXCLUDED.last_scan_at",
        (now,),
    )


def status_rows(connection, today):
    counts = connection.execute(
        "SELECT state,count(*) AS n FROM collection_schedule_items "
        "WHERE state!='resolved' AND (business_date=%s OR state IN ('queued','running','needs_attention')) "
        "GROUP BY state",
        (today,),
    ).fetchall()
    attention = connection.execute(
        "SELECT id,account_id,run_id,reason_codes,updated_at FROM collection_schedule_items "
        "WHERE state='needs_attention' ORDER BY updated_at DESC,id LIMIT 5"
    ).fetchall()
    last = connection.execute(
        "SELECT last_scan_at FROM collection_schedule_state WHERE id=1"
    ).fetchone()
    return counts, attention, last
