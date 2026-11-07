"""WhatsApp account bindings and durable transport records."""


def connection_for(connection, account_id):
    return connection.execute(
        "SELECT * FROM collection_whatsapp_connections WHERE account_id=%s", (account_id,)
    ).fetchone()


def delivery_for(connection, message_id):
    return connection.execute(
        "SELECT * FROM collection_whatsapp_deliveries WHERE message_id=%s", (message_id,)
    ).fetchone()
