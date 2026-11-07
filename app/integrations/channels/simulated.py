from app.shared.errors import DomainError


class SimulatedChatAdapter:
    name = "simulated_chat"

    def capabilities(self):
        return ["synthetic_dispatch", "synthetic_inbound"]

    def dispatch(self, authorized_intent):
        # Pure deterministic simulator; receipt persistence and idempotency belong to service.
        return {"id": "sim-" + authorized_intent.message_id, "status": "simulated_sent"}

    def normalize_verified_event(self, event):
        raise DomainError("synthetic_only", "Use explicit synthetic inbound endpoint", 422)
