"""Shared persistence and escalation for accepted inbound messages."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from app.modules.audit.service import append_event
from app.modules.compliance.screening import inbound_categories
from app.modules.reviews.service import open_review


def store_inbound(connection, account_id, text, caller, now, channel, scenario=None):
    message = {
        "id": str(uuid4()), "account_id": account_id, "run_id": None,
        "channel": channel, "direction": "inbound", "text": text,
        "status": "received", "version": 1, "citation_ids": [], "created_at": now.isoformat(),
    }
    connection.execute(
        "INSERT INTO collection_messages VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (message["id"], account_id, None, channel, "inbound", text, "received", 1, Jsonb([]), now),
    )
    categories = inbound_categories(text)
    if scenario in ("hardship", "dispute", "human_request"):
        categories = sorted(set(categories) | {scenario})
    if categories:
        kind = "dispute" if "dispute" in categories else "hardship"
        open_review(connection, account_id, kind, now)
    if {"stop_request", "third_party_responder"} & set(categories):
        connection.execute(
            "UPDATE collection_authorities SET opted_out=true WHERE account_id=%s", (account_id,)
        )
    append_event(connection, account_id, "message_received", caller.actor_kind, "received", now)
    return message
