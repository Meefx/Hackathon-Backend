"""Deterministic screening shared by all collection candidate entry points."""

from app.trust.safety import screen_message
from app.trust.screening import MockLayer2


def screen_candidate(text: str, *, mode: str, fixture_enabled: bool) -> dict:
    # No live classifier has been integrated. Only explicit simulated fixture mode
    # may use the deterministic test classifier, including producer-backed evidence.
    label = MockLayer2().evaluate(text) if mode == "simulated" and fixture_enabled else None
    return screen_message(text, label)


def attest_candidate(connection, message, decision, evidence, settings, now, mode):
    from app.modules.compliance.service import issue_approval

    screening = screen_candidate(
        message["text"], mode=mode, fixture_enabled=settings.demo_fixture_enabled
    )
    if screening["result"] != "ALLOW":
        return False
    return issue_approval(connection, message, decision, evidence, settings, now, mode)


def inbound_categories(text: str) -> list[str]:
    import re

    patterns = {
        "third_party_responder": r"\b(wife|husband|not the borrower|wrong number|istrinya|suaminya|bukan orangnya)\b",
        "stop_request": r"\b(stop|unsubscribe|do not contact|jangan hubungi|berhenti)\b",
        "dispute": r"\b(not my debt|already paid|wrong amount|fraud|bukan utang|sudah bayar|sudah lunas)\b",
        "hardship": r"\b(lost my job|cannot pay|hospital|bereavement|phk|rumah sakit|tidak ada uang)\b",
        "human_request": r"\b(human|officer|petugas)\b",
        "distress": r"\b(kill myself|suicide|want to die|lebih baik mati|bunuh diri)\b",
    }
    return sorted(
        name for name, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)
    )
