"""Only this orchestration service issues approval tokens after screening."""

from app.trust.safety import issue_approval, screen_message


class MockLayer2:
    """Deterministic simulator, never presented as a validated live classifier."""

    profile = "mock-policy-v1"

    def evaluate(self, text):
        return {
            "violation": 0,
            "policy_category": None,
            "rule_ids": [],
            "confidence": 0.99,
            "rationale": "Synthetic test fixture",
        }


def approve_candidate(*, secret, attempt_id, case_id, text, evidence, now, layer2):
    try:
        label = layer2.evaluate(text)
    except Exception:  # noqa: BLE001 - every classifier failure must fail closed
        label = None
    decision = screen_message(text, label, source="template", layer2_exempt=False)
    token = None
    if decision["result"] == "ALLOW":
        token = issue_approval(secret, attempt_id, case_id, text, evidence, now)
    return decision, token
