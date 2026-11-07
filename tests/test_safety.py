import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.trust.errors import TrustError
from app.trust.safety import (
    evaluate_contact,
    issue_approval,
    render_candidate,
    screen_message,
    verify_approval,
)

NOW = datetime(2026, 11, 9, 3, tzinfo=UTC)
SECRET = "test-signing-secret-is-at-least-thirty-two-bytes"
SAFE = {
    "violation": 0,
    "policy_category": None,
    "rule_ids": [],
    "confidence": 0.95,
    "rationale": "Safe reminder.",
}
EVIDENCE = [{"chunk_id": "chunk-a", "version": 1, "sha256": "a" * 64}]


def contact(**overrides):
    context = {
        "is_synthetic": True,
        "timezone": "Asia/Jakarta",
        "calendar_years": [2026, 2027],
        "holidays": ["2026-12-24", "2026-12-25"],
        "state": "active",
        "purpose": "proactive",
        "borrower_owned": True,
        "verified": True,
        "opted_out": False,
        "channel": "sim_chat",
        "data_verified_at": (NOW - timedelta(hours=1)).isoformat(),
        "proactive_history": [],
    }
    context.update(overrides)
    return context


@pytest.mark.parametrize(
    "instant,allowed,reason",
    [
        ("2026-11-07T07:00:00+00:00", True, None),
        ("2026-11-07T12:59:59+00:00", True, None),
        ("2026-11-07T13:00:00+00:00", False, "OUTSIDE_HOURS"),
        ("2026-11-09T00:59:59+00:00", False, "OUTSIDE_HOURS"),
        ("2026-11-09T01:00:00+00:00", True, None),
        ("2026-11-08T03:00:00+00:00", False, "SUNDAY"),
        ("2026-12-24T03:00:00+00:00", False, "HOLIDAY"),
        ("2026-12-25T03:00:00+00:00", False, "HOLIDAY"),
        ("2028-01-03T03:00:00+00:00", False, "CALENDAR_MISSING"),
    ],
)
def test_scheduler_boundaries(instant, allowed, reason):
    now = datetime.fromisoformat(instant)
    decision = evaluate_contact(contact(data_verified_at=now.isoformat()), now)
    assert decision["allowed"] is allowed
    if reason:
        assert reason in decision["reason_codes"]


def test_timezone_resolution_and_unknown_fail_closed():
    early = datetime(2026, 11, 9, 0, 30, tzinfo=UTC)
    assert evaluate_contact(
        contact(timezone="Asia/Makassar", data_verified_at=early.isoformat()), early
    )["allowed"]
    evening = datetime(2026, 11, 9, 11, 30, tzinfo=UTC)
    assert (
        "OUTSIDE_HOURS"
        in evaluate_contact(
            contact(timezone="Asia/Jayapura", data_verified_at=evening.isoformat()), evening
        )["reason_codes"]
    )
    for zone in (None, "", "Unknown/Zone", "Europe/London"):
        assert (
            "TIMEZONE_UNVERIFIED" in evaluate_contact(contact(timezone=zone), NOW)["reason_codes"]
        )


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("is_synthetic", False, "SIMULATION_REQUIRED"),
        ("is_synthetic", 1, "SIMULATION_REQUIRED"),
        ("borrower_owned", False, "NO_AUTHORISED_RECIPIENT"),
        ("verified", False, "NO_AUTHORISED_RECIPIENT"),
        ("opted_out", True, "NO_AUTHORISED_RECIPIENT"),
        ("channel", "whatsapp", "CHANNEL_DISABLED"),
        ("channel", "sms", "CHANNEL_DISABLED"),
        ("state", "paused", "CASE_NOT_ACTIVE"),
        ("state", "escalated", "CASE_NOT_ACTIVE"),
        ("state", "disputed", "CASE_NOT_ACTIVE"),
        ("state", "human_handling", "CASE_NOT_ACTIVE"),
        ("state", "resolved", "CASE_NOT_ACTIVE"),
        ("state", "unknown", "CASE_NOT_ACTIVE"),
        ("data_verified_at", None, "DATA_INCOMPLETE"),
        ("data_verified_at", (NOW - timedelta(hours=25)).isoformat(), "DATA_STALE"),
        ("data_verified_at", (NOW + timedelta(hours=1)).isoformat(), "DATA_INVALID"),
        ("purpose", "unknown", "PURPOSE_INVALID"),
    ],
)
def test_contact_denials(field, value, reason):
    result = evaluate_contact(contact(**{field: value}), NOW)
    assert not result["allowed"]
    assert reason in result["reason_codes"]
    assert result["next_eligible_at"] is None


@pytest.mark.parametrize("purpose", ["proactive", "reply", "officer", "ack"])
def test_escalation_cannot_be_bypassed_by_purpose(purpose):
    result = evaluate_contact(contact(state="escalated", purpose=purpose), NOW)
    assert not result["allowed"]


def test_proactive_caps_and_reply_exemption():
    history = [(NOW - timedelta(minutes=30)).isoformat()]
    result = evaluate_contact(contact(proactive_history=history), NOW)
    assert "FREQUENCY_CAP_DAILY" in result["reason_codes"]
    assert datetime.fromisoformat(result["next_eligible_at"]) == datetime(
        2026, 11, 10, 1, tzinfo=UTC
    )
    assert evaluate_contact(
        contact(state="in_conversation", purpose="reply", proactive_history=history), NOW
    )["allowed"]
    assert not evaluate_contact(contact(state="in_conversation"), NOW)["allowed"]


def test_rolling_cap_expires_at_exact_168_hours():
    now = datetime(2026, 11, 12, 3, tzinfo=UTC)
    history = [datetime(2026, 11, day, 3, tzinfo=UTC).isoformat() for day in (9, 10, 11)]
    result = evaluate_contact(
        contact(data_verified_at=now.isoformat(), proactive_history=history), now
    )
    assert "FREQUENCY_CAP_WEEKLY" in result["reason_codes"]
    assert datetime.fromisoformat(result["next_eligible_at"]) == datetime(
        2026, 11, 16, 3, tzinfo=UTC
    )
    expiry = datetime(2026, 11, 16, 3, tzinfo=UTC)
    assert evaluate_contact(
        contact(data_verified_at=expiry.isoformat(), proactive_history=history), expiry
    )["allowed"]


def test_next_window_skips_sunday_and_holidays():
    saturday = datetime(2026, 11, 7, 13, tzinfo=UTC)
    result = evaluate_contact(contact(data_verified_at=saturday.isoformat()), saturday)
    assert datetime.fromisoformat(result["next_eligible_at"]) == datetime(
        2026, 11, 9, 1, tzinfo=UTC
    )
    holiday = datetime(2026, 12, 24, 3, tzinfo=UTC)
    result = evaluate_contact(contact(data_verified_at=holiday.isoformat()), holiday)
    assert datetime.fromisoformat(result["next_eligible_at"]) == datetime(
        2026, 12, 26, 1, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "change",
    [
        {"data_verified_at": "invalid"},
        {"proactive_history": ["invalid"]},
        {"holidays": ["invalid"]},
        {"calendar_years": None},
        {"proactive_history": [(NOW + timedelta(hours=1)).isoformat()]},
    ],
)
def test_invalid_inputs_fail_closed(change):
    assert not evaluate_contact(contact(**change), NOW)["allowed"]


def test_naive_clock_and_non_dict_fail_closed():
    assert not evaluate_contact(contact(), NOW.replace(tzinfo=None))["allowed"]
    assert not evaluate_contact(None, NOW)["allowed"]


def test_render_verified_facts_only():
    text = render_candidate(
        "Your balance is {{amount_due}}, due {{due_date}}.",
        {"amount_due": "IDR 1,250,000", "due_date": "1 November 2026"},
    )
    assert text == "Your balance is IDR 1,250,000, due 1 November 2026."
    assert render_candidate("One clear next step.", {}) == "One clear next step."


@pytest.mark.parametrize(
    "body,facts,code",
    [
        ("Your balance is 500.", {}, "UNSOURCED_NUMBER"),
        ("Pay Rp tomorrow.", {}, "UNSOURCED_NUMBER"),
        ("Pay rupiah tomorrow.", {}, "UNSOURCED_NUMBER"),
        ("Pay IDR tomorrow.", {}, "UNSOURCED_NUMBER"),
        ("Discount 5%.", {}, "UNSOURCED_NUMBER"),
        ("Pay fifty dollars.", {}, "UNSOURCED_NUMBER"),
        ("Bayar satu juta rupiah.", {}, "UNSOURCED_NUMBER"),
        ("Pay {{bank_account_number}}.", {"bank_account_number": "123"}, "UNKNOWN_PLACEHOLDER"),
        ("Pay {{amount_due}}.", {}, "UNKNOWN_PLACEHOLDER"),
        ("Pay {{amount_due}}.", {"amount_due": 100}, "INVALID_FACT"),
        ("Pay {{amount_due}}.", {"amount_due": "{{other}}"}, "INVALID_FACT"),
        ("Pay {{amount_due.", {}, "INVALID_PLACEHOLDER"),
        ("x" * 401, {}, "MESSAGE_TOO_LONG"),
        ("{{payment_instructions}}", {"payment_instructions": "x" * 401}, "MESSAGE_TOO_LONG"),
    ],
)
def test_render_rejects_untrusted_content(body, facts, code):
    with pytest.raises(TrustError) as caught:
        render_candidate(body, facts)
    assert caught.value.code == code


def test_proposed_actions_must_be_allowlisted():
    assert render_candidate("Please use the official app.", {}, ["offer_human"], ["offer_human"])
    with pytest.raises(TrustError):
        render_candidate("Please use the official app.", {}, ["offer_human"], ["waive_fee"])
    with pytest.raises(TrustError):
        render_candidate("Please use the official app.", {}, proposed_actions=["waive_fee"])


LAYER1_CASES = [
    ("dilaporkan ke polisi", "C1"),
    ("lapor polisi", "C1"),
    ("dipenjara", "C1"),
    ("penjara", "C1"),
    ("masuk bui", "C1"),
    ("dipidanakan", "C1"),
    ("digugat", "C1"),
    ("akan kami tuntut", "C1"),
    ("blacklist", "C1"),
    ("daftar hitam", "C1"),
    ("tim lapangan akan datang", "C1"),
    ("debt collector akan datang", "C1"),
    ("kami datangi", "C1"),
    ("disebarkan", "C1"),
    ("sebar data", "C1"),
    ("viral", "C1"),
    ("kalau tidak bayar", "C1"),
    ("jika tidak membayar besok kami datang", "C1"),
    ("jika tidak membayar besok akan datang", "C1"),
    ("police", "C1"),
    ("jail", "C1"),
    ("lawsuit", "C1"),
    ("We will sue you.", "C1"),
    ("We will visit your home.", "C1"),
    ("We will expose your debt.", "C1"),
    ("pukul", "C2"),
    ("hajar", "C2"),
    ("bakar", "C2"),
    ("rusak rumah", "C2"),
    ("celaka", "C2"),
    ("habisi", "C2"),
    ("We will hurt you.", "C2"),
    ("penipu", "C3"),
    ("maling", "C3"),
    ("tidak tahu malu", "C3"),
    ("pemalas", "C3"),
    ("bodoh", "C3"),
    ("goblok", "C3"),
    ("miskin", "C3"),
    ("orang seperti anda", "C3"),
    ("memalukan", "C3"),
    ("You are a lazy fraud.", "C3"),
    ("bayar sekarang juga", "C4"),
    ("tidak ada alasan", "C4"),
    ("wajib bayar hari ini", "C4"),
    ("terakhir kali kami ingatkan", "C4"),
    ("pinjam ke tempat lain", "C4"),
    ("Pay now!!!", "C4"),
    ("No excuses.", "C4"),
    ("Borrow elsewhere to pay.", "C4"),
    ("keluarga anda", "C5"),
    ("atasan anda", "C5"),
    ("kantor anda", "C5"),
    ("rekan kerja", "C5"),
    ("tetangga", "C5"),
    ("kontak darurat", "C5"),
    ("hubungi istri", "C5"),
    ("hubungi suami", "C5"),
    ("We will contact your employer.", "C5"),
    ("We will tell your family.", "C5"),
    ("suku anda", "C6"),
    ("agama anda", "C6"),
    ("ras anda", "C6"),
    ("orang seperti kalian", "C6"),
    ("Your religion makes you unreliable.", "C6"),
    ("I am a human collector.", "C7"),
    ("Your debt is cleared.", "C7"),
    ("diskon", "C8"),
    ("potongan", "C8"),
    ("hapus denda", "C8"),
    ("bebas bunga", "C8"),
    ("keringanan disetujui", "C8"),
    ("restrukturisasi disetujui", "C8"),
    ("pelunasan sebagian diterima", "C8"),
    ("perpanjangan", "C8"),
    ("We waive your fee.", "C8"),
    ("Your discount is approved.", "C8"),
    ("Your extension is approved.", "C8"),
    ("kode otp", "C9"),
    ("otp anda", "C9"),
    ("pin anda", "C9"),
    ("password", "C9"),
    ("kata sandi", "C9"),
    ("nomor kartu lengkap", "C9"),
    ("cvv", "C9"),
    ("Send your PIN.", "C9"),
]


@pytest.mark.parametrize("text,category", LAYER1_CASES)
def test_every_layer1_pattern_blocks_even_safe_classifier(text, category):
    result = screen_message(text, SAFE)
    assert result["result"] == "BLOCK"
    assert category in result["categories"]


def test_layer1_normalizes_case_accents_and_whitespace():
    assert screen_message("We will contact YOUR\n  ÉMPLOYER.", SAFE)["result"] == "BLOCK"


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"violation": "0"},
        {**SAFE, "confidence": float("nan")},
        {**SAFE, "confidence": 1.1},
        {**SAFE, "violation": True},
        {**SAFE, "policy_category": "C99"},
        {**SAFE, "violation": 1, "policy_category": []},
        {**SAFE, "violation": 1, "policy_category": {}},
        {**SAFE, "rule_ids": "C1"},
        {**SAFE, "policy_category": "C1"},
        {**SAFE, "rationale": "x " * 31},
    ],
)
def test_layer2_invalid_output_fails_closed(value):
    result = screen_message("Please use the official app.", value)
    assert result["result"] == "BLOCK"
    assert "GUARDRAIL_UNAVAILABLE" in result["reason_codes"]


@pytest.mark.parametrize(
    "source,exempt,expected",
    [
        ("model", False, "BLOCK"),
        ("model", True, "BLOCK"),
        ("officer", False, "BLOCK"),
        ("officer", True, "BLOCK"),
        ("template", False, "BLOCK"),
        ("template", True, "ALLOW"),
    ],
)
def test_only_registered_exempt_templates_survive_unavailable_layer2(source, exempt, expected):
    assert (
        screen_message("Please use the official app.", None, source=source, layer2_exempt=exempt)[
            "result"
        ]
        == expected
    )
    assert (
        screen_message("You are a lazy fraud.", None, source=source, layer2_exempt=exempt)["result"]
        == "BLOCK"
    )


@pytest.mark.parametrize(
    "violation,category,confidence,expected",
    [
        (0, None, 0.95, "ALLOW"),
        (0, None, 0.5, "ESCALATE"),
        (1, "C1", 0.95, "BLOCK"),
        (1, "C8", 0.7, "ESCALATE"),
        (1, "C8", 0.8, "BLOCK"),
        (1, "C10", 0.95, "ESCALATE"),
    ],
)
def test_guardrail_combination(violation, category, confidence, expected):
    result = screen_message(
        "Please use the official app.",
        {**SAFE, "violation": violation, "policy_category": category, "confidence": confidence},
    )
    assert result["result"] == expected


def sign(**changes):
    args = {
        "secret": SECRET,
        "attempt_id": "a1",
        "case_id": "case1",
        "text": "Please use the app.",
        "evidence": EVIDENCE,
        "now": NOW,
    }
    args.update(changes)
    return issue_approval(**args)


def verify(token, **changes):
    args = {
        "token": token,
        "secret": SECRET,
        "attempt_id": "a1",
        "case_id": "case1",
        "text": "Please use the app.",
        "evidence": EVIDENCE,
        "now": NOW,
    }
    args.update(changes)
    return verify_approval(**args)


def test_hmac_token_roundtrip_and_exact_text_evidence_binding():
    token = sign()
    assert verify(token) is None
    for changes in (
        {"text": "Please use the app!"},
        {"attempt_id": "a2"},
        {"case_id": "case2"},
        {"secret": "a" * 48},
        {"evidence": [{**EVIDENCE[0], "version": 2}]},
        {"policy_version": "v2"},
    ):
        with pytest.raises(TrustError):
            verify(token, **changes)


def test_token_expiry_future_issue_and_invalid_encoding_fail_closed():
    token = sign()
    assert verify(token, now=NOW + timedelta(seconds=119)) is None
    for now in (NOW + timedelta(seconds=120), NOW - timedelta(seconds=1)):
        with pytest.raises(TrustError):
            verify(token, now=now)
    for invalid in ("", "garbage", token + "x", "a.b.c", None):
        with pytest.raises(TrustError):
            verify(invalid)


def test_signing_rejects_weak_secret_and_invalid_ttl():
    for changes in (
        {"secret": "short"},
        {"ttl_s": 0},
        {"ttl_s": 121},
        {"now": NOW.replace(tzinfo=None)},
    ):
        with pytest.raises(TrustError):
            sign(**changes)


def test_dispatch_rechecks_clock_after_blocking_state_locks(monkeypatch):
    """A lock wait crossing closing time invalidates an earlier approval."""
    from app.services import dispatch_gate as gate
    from app.trust.security import Actor

    before_close = datetime(2026, 11, 9, 12, 59, 59, tzinfo=UTC)
    after_close = before_close + timedelta(seconds=2)
    clock = SimpleNamespace(value=before_close)
    clock.now = lambda: clock.value
    actor = Actor("synthetic-clock-test", "operator", "cf", "spaylater")
    attempt_id = UUID("10000000-0000-0000-0000-000000000001")
    case_id = UUID("20000000-0000-0000-0000-000000000001")
    template_id = UUID("30000000-0000-0000-0000-000000000001")
    evidence = [{"id": "40000000-0000-0000-0000-000000000001", "version": 1}]
    attempt = {
        "id": attempt_id,
        "status": "APPROVED",
        "evidence": evidence,
        "template_id": template_id,
        "case_id": case_id,
        "case_version": 1,
        "text_sha256": hashlib.sha256(b"Please use the app.").hexdigest(),
        "policy_version": "collections-policy-v1",
    }
    case = {"id": case_id, "version": 1, "borrower_ref": "synthetic-borrower"}

    class FakeConnection:
        def execute(self, statement, params=()):
            row = attempt if "FROM trust.attempts" in statement else {"id": "locked"}
            return SimpleNamespace(fetchone=lambda: row)

    def blocking_case_lock(*args, **kwargs):
        clock.value = after_close
        return case

    deliveries = []
    monkeypatch.setattr(gate, "scoped_case", blocking_case_lock)
    monkeypatch.setattr(gate, "get_template", lambda *args: {"evidence": evidence})
    monkeypatch.setattr(
        gate,
        "contact_context",
        lambda *args: contact(data_verified_at=before_close.isoformat()),
    )
    monkeypatch.setattr(gate, "append_event", lambda *args: None)
    monkeypatch.setattr(gate, "verify_approval", lambda *args: None)
    monkeypatch.setattr(gate, "deliver", lambda *args: deliveries.append(args) or "delivery")

    result = gate.dispatch(
        FakeConnection(),
        actor,
        {"attempt_id": str(attempt_id), "text": "Please use the app.", "token": "test"},
        SECRET,
        clock,
    )
    assert result["status"] == "NOT_SENT"
    assert "OUTSIDE_HOURS" in result["reason_codes"]
    assert deliveries == []
