"""Persistence for immutable per-run evidence snapshots."""

from psycopg.types.json import Jsonb


def save_snapshot(connection, snapshot):
    connection.execute(
        "INSERT INTO knowledge_snapshots(id,run_id,status,source_mode,required,retrieved_at,"
        "snapshot_hash,sources,reason_code,critical_context,verification_profile,"
        "snapshot_hash_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            snapshot["id"],
            snapshot["run_id"],
            snapshot["status"],
            snapshot["source_mode"],
            snapshot["required"],
            snapshot["retrieved_at"],
            snapshot["snapshot_hash"],
            Jsonb(snapshot["sources"]),
            snapshot["reason_code"],
            Jsonb(snapshot["critical_context"]),
            Jsonb(snapshot["verification_profile"]),
            snapshot["snapshot_hash_version"],
        ),
    )


def run_snapshots(connection, run_id):
    return connection.execute(
        "SELECT * FROM knowledge_snapshots WHERE run_id=%s ORDER BY created_sequence", (run_id,)
    ).fetchall()


def snapshot_page(connection, run_id, limit, offset):
    items = connection.execute(
        "SELECT * FROM knowledge_snapshots WHERE run_id=%s "
        "ORDER BY retrieved_at,id LIMIT %s OFFSET %s",
        (run_id, limit, offset),
    ).fetchall()
    count = connection.execute(
        "SELECT count(*) AS n FROM knowledge_snapshots WHERE run_id=%s",
        (run_id,),
    ).fetchone()["n"]
    return items, count
