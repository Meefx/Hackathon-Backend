"""Persist exact template and run-evidence bindings separately from message text."""

from typing import Any

from psycopg.types.json import Jsonb
from pydantic import TypeAdapter


def message_binding(connection, message_id: str, *, lock: bool = False) -> dict | None:
    return connection.execute(
        "SELECT * FROM collection_message_bindings WHERE message_id=%s"
        + (" FOR UPDATE" if lock else ""),
        (message_id,),
    ).fetchone()


def save_binding(connection, message_id: str, evidence: dict, template: dict | None) -> None:
    connection.execute(
        "INSERT INTO collection_message_bindings "
        "(message_id,template_binding,snapshot_hash,evidence_references) VALUES (%s,%s,%s,%s)",
        (
            message_id,
            Jsonb(template) if template else None,
            evidence["snapshot_hash"],
            Jsonb(evidence["references"]),
        ),
    )


def template_binding(template):
    return TypeAdapter(Any).dump_python(template, mode="json") if template else None
