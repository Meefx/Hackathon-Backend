"""Pure, fail-closed controls. Model labels and retrieved text are untrusted."""

import base64
import hashlib
import hmac
import json
import math
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.trust.errors import TrustError

POLICY = "collections-policy-v1"
ZONES = {"Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura"}
PLACEHOLDERS = {
    "amount_due",
    "due_date",
    "borrower_first_name",
    "lender_name",
    "product_name",
    "payment_instructions",
    "ptp_date",
}
ACTIONS = {"offer_human", "payment_instructions", "record_promise_to_pay", "create_review_task"}
_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
_NUMERIC = re.compile(
    r"\d|\b(?:rp|rupiah|idr|usd|percent|persen)\b|[%$€£]|"
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|"
    r"satu|dua|tiga|empat|lima|enam|tujuh|delapan|sembilan|sepuluh|sebelas|seratus|seribu|sejuta)"
    r"(?:\s+\w+)?\s+(?:dollars?|days?|weeks?|months?|hours?|hari|minggu|bulan|jam|puluh|belas|ratus|ribu|juta|miliar|rupiah|persen)\b",
    re.IGNORECASE,
)
_PATTERNS = {
    "C1": [
        r"dilaporkan ke polisi",
        r"lapor polisi",
        r"dipenjara",
        r"penjara",
        r"masuk bui",
        r"dipidanakan",
        r"digugat",
        r"akan kami tuntut",
        r"blacklist",
        r"daftar hitam",
        r"tim lapangan akan datang",
        r"debt collector akan datang",
        r"kami datangi",
        r"disebarkan",
        r"sebar data",
        r"viral",
        r"kalau tidak bayar",
        r"jika tidak membayar.*\b(?:kami|akan)\b",
        r"police",
        r"jail",
        r"lawsuit",
        r"(?:we will |will |can )sue",
        r"(?:we will |will )visit your",
        r"expose your debt",
    ],
    "C2": [r"pukul", r"hajar", r"bakar", r"rusak rumah", r"celaka", r"habisi", r"hurt you"],
    "C3": [
        r"penipu",
        r"maling",
        r"tidak tahu malu",
        r"pemalas",
        r"bodoh",
        r"goblok",
        r"miskin",
        r"orang seperti anda",
        r"memalukan",
        r"lazy fraud",
        r"you are (?:lazy|stupid)",
    ],
    "C4": [
        r"bayar sekarang juga",
        r"tidak ada alasan",
        r"wajib bayar hari ini",
        r"terakhir kali kami ingatkan",
        r"pinjam ke tempat lain",
        r"no excuses",
        r"borrow elsewhere",
        r"pay (?:right )?now",
    ],
    "C5": [
        r"keluarga anda",
        r"atasan anda",
        r"kantor anda",
        r"rekan kerja",
        r"tetangga",
        r"kontak darurat",
        r"hubungi istri",
        r"hubungi suami",
        r"(?:contact|tell|inform|visit) your (?:employer|family|spouse|boss|neighbor|neighbour)",
        r"kami kabari.*(?:keluarga|istri|suami|atasan|kantor)",
    ],
    "C6": [
        r"suku anda",
        r"agama anda",
        r"ras anda",
        r"orang seperti kalian",
        r"your (?:religion|race|ethnicity).*unreliable",
    ],
    "C7": [r"i am a human", r"your debt is (?:cleared|paid|cancelled)", r"utang anda lunas"],
    "C8": [
        r"diskon",
        r"potongan",
        r"hapus denda",
        r"bebas bunga",
        r"keringanan disetujui",
        r"restrukturisasi disetujui",
        r"pelunasan sebagian diterima",
        r"perpanjangan",
        r"waive your fee",
        r"(?:discount|extension|restructuring|settlement) is approved",
    ],
    "C9": [
        r"kode otp",
        r"otp anda",
        r"pin anda",
        r"password",
        r"kata sandi",
        r"nomor kartu lengkap",
        r"cvv",
        r"your pin",
        r"your otp",
        r"full card number",
    ],
}


def _aware(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timezone required")
    return result


def _temporal_reasons(local, holidays, years, history, proactive):
    reasons = []
    if local.year not in years:
        reasons.append("CALENDAR_MISSING")
    if local.weekday() == 6:
        reasons.append("SUNDAY")
    if local.date() in holidays:
        reasons.append("HOLIDAY")
    if not time(8) <= local.time().replace(tzinfo=None) < time(20):
        reasons.append("OUTSIDE_HOURS")
    if proactive:
        if any(item.astimezone(local.tzinfo).date() == local.date() for item in history):
            reasons.append("FREQUENCY_CAP_DAILY")
        if sum(local - timedelta(hours=168) < item <= local for item in history) >= 3:
            reasons.append("FREQUENCY_CAP_WEEKLY")
    return reasons


def _next_window(local, holidays, years, history, proactive):
    candidate = local
    for _ in range(740):
        if candidate.year not in years:
            return None
        reasons = _temporal_reasons(candidate, holidays, years, history, proactive)
        if not reasons:
            return candidate.isoformat()
        if "FREQUENCY_CAP_WEEKLY" in reasons:
            recent = sorted(
                item for item in history if candidate - timedelta(hours=168) < item <= candidate
            )
            expiry = (recent[len(recent) - 3] + timedelta(hours=168)).astimezone(local.tzinfo)
            candidate = max(candidate, expiry)
            continue
        if candidate.time().replace(tzinfo=None) < time(8) and not set(reasons) - {"OUTSIDE_HOURS"}:
            candidate = candidate.replace(hour=8, minute=0, second=0, microsecond=0)
        else:
            candidate = (candidate + timedelta(days=1)).replace(
                hour=8, minute=0, second=0, microsecond=0
            )
    return None


def evaluate_contact(context, now):
    try:
        now = _aware(now)
        if not isinstance(context, dict):
            raise TypeError("Invalid context")
        reasons = []
        if context.get("is_synthetic") is not True:
            reasons.append("SIMULATION_REQUIRED")
        zone = context.get("timezone")
        if zone not in ZONES:
            reasons.append("TIMEZONE_UNVERIFIED")
        if (
            context.get("borrower_owned") is not True
            or context.get("verified") is not True
            or context.get("opted_out") is not False
        ):
            reasons.append("NO_AUTHORISED_RECIPIENT")
        if context.get("channel") != "sim_chat":
            reasons.append("CHANNEL_DISABLED")
        purpose = context.get("purpose")
        if purpose not in {"proactive", "reply", "officer", "ack"}:
            reasons.append("PURPOSE_INVALID")
        state = context.get("state")
        if state not in {"active", "in_conversation", "promise_to_pay"} or (
            purpose == "proactive" and state != "active"
        ):
            reasons.append("CASE_NOT_ACTIVE")
        if not context.get("data_verified_at"):
            reasons.append("DATA_INCOMPLETE")
        else:
            age = now - _aware(context["data_verified_at"])
            if age < timedelta(0):
                reasons.append("DATA_INVALID")
            elif age > timedelta(hours=24):
                reasons.append("DATA_STALE")
        years = context["calendar_years"]
        if not isinstance(years, list) or any(type(year) is not int for year in years):
            raise ValueError("Invalid calendar")
        holidays = {date.fromisoformat(item) for item in context["holidays"]}
        history = [_aware(item) for item in context["proactive_history"]]
        if any(item > now for item in history):
            raise ValueError("Future contact")
        if reasons:
            return {"allowed": False, "reason_codes": reasons, "next_eligible_at": None}
        local = now.astimezone(ZoneInfo(zone))
        reasons = _temporal_reasons(local, holidays, years, history, purpose == "proactive")
        return {
            "allowed": not reasons,
            "reason_codes": reasons,
            "next_eligible_at": _next_window(
                local, holidays, years, history, purpose == "proactive"
            )
            if reasons
            else None,
        }
    except Exception:  # noqa: BLE001 - malformed eligibility inputs always deny
        return {"allowed": False, "reason_codes": ["SCHEDULER_ERROR"], "next_eligible_at": None}


def render_candidate(body, facts, allowed_actions=None, proposed_actions=None):
    if not isinstance(body, str) or not body or not isinstance(facts, dict):
        raise TrustError("INVALID_CANDIDATE", 422)
    if len(body) > 400:
        raise TrustError("MESSAGE_TOO_LONG", 422)
    if not set(proposed_actions or []).issubset(set(allowed_actions or []) & ACTIONS):
        raise TrustError("ACTION_NOT_ALLOWED")
    masked = _PLACEHOLDER.sub("", body)
    if "{" in masked or "}" in masked:
        raise TrustError("INVALID_PLACEHOLDER")
    if _NUMERIC.search(masked):
        raise TrustError("UNSOURCED_NUMBER")

    def substitute(match):
        name = match[1]
        if name not in PLACEHOLDERS or name not in facts:
            raise TrustError("UNKNOWN_PLACEHOLDER")
        value = facts[name]
        if not isinstance(value, str) or "{" in value or "}" in value:
            raise TrustError("INVALID_FACT")
        return value

    text = _PLACEHOLDER.sub(substitute, body)
    if len(text) > 400:
        raise TrustError("MESSAGE_TOO_LONG")
    return text


def _normal(text):
    normalized = unicodedata.normalize("NFKD", text).casefold()
    normalized = "".join(
        c for c in normalized if not unicodedata.combining(c) and unicodedata.category(c) != "Cf"
    )
    return " ".join(normalized.split())


def screen_message(text, layer2_result, *, source="model", layer2_exempt=False):
    def result(outcome, categories=None, codes=None):
        return {
            "result": outcome,
            "categories": categories or [],
            "reason_codes": codes or [],
            "policy_version": POLICY,
        }

    if not isinstance(text, str) or not text or len(text) > 400:
        return result(
            "BLOCK", codes=["MESSAGE_TOO_LONG" if isinstance(text, str) else "INVALID_TEXT"]
        )
    normalized = _normal(text)
    categories = [
        category
        for category, patterns in _PATTERNS.items()
        if any(re.search(r"\b(?:" + pattern + r")\b", normalized) for pattern in patterns)
    ]
    if text.count("!") >= 3 and "C4" not in categories:
        categories.append("C4")
    if categories:
        return result("BLOCK", categories, ["LAYER1_BLOCK"])
    label = layer2_result
    valid = isinstance(label, dict) and set(label) == {
        "violation",
        "policy_category",
        "rule_ids",
        "confidence",
        "rationale",
    }
    if valid:
        valid = (
            type(label["violation"]) is int
            and label["violation"] in {0, 1}
            and type(label["confidence"]) in {int, float}
            and math.isfinite(label["confidence"])
            and 0 <= label["confidence"] <= 1
            and isinstance(label["rule_ids"], list)
            and all(isinstance(rule, str) and len(rule) <= 40 for rule in label["rule_ids"])
            and isinstance(label["rationale"], str)
            and len(label["rationale"].split()) <= 30
            and (
                (label["violation"] == 0 and label["policy_category"] is None)
                or (
                    label["violation"] == 1
                    and isinstance(label["policy_category"], str)
                    and label["policy_category"] in {f"C{i}" for i in range(1, 11)}
                )
            )
        )
    if not valid:
        if source == "template" and layer2_exempt is True:
            return result("ALLOW", codes=["APPROVED_EXEMPT_TEMPLATE"])
        return result("BLOCK", codes=["GUARDRAIL_UNAVAILABLE"])
    category, confidence = label["policy_category"], label["confidence"]
    if label["violation"] == 1:
        escalate = category == "C10" or (category == "C8" and confidence < 0.8)
        return result("ESCALATE" if escalate else "BLOCK", [category], ["LAYER2_VIOLATION"])
    return result("ESCALATE" if confidence < 0.6 else "ALLOW", codes=[])


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _approval_payload(attempt_id, case_id, text, evidence, policy_version):
    if not isinstance(text, str) or not isinstance(evidence, list) or not evidence:
        raise TrustError("APPROVAL_INPUT_INVALID")
    return {
        "attempt_id": attempt_id,
        "case_id": case_id,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "evidence_sha256": hashlib.sha256(_canonical(evidence).encode()).hexdigest(),
        "policy_version": policy_version,
    }


def _key(secret):
    if not isinstance(secret, str) or len(secret.encode()) < 32:
        raise TrustError("APPROVAL_CONFIGURATION_INVALID", 503)
    return secret.encode()


def issue_approval(
    secret, attempt_id, case_id, text, evidence, now, policy_version=POLICY, ttl_s=120
):
    key = _key(secret)
    if type(ttl_s) is not int or not 1 <= ttl_s <= 120:
        raise TrustError("APPROVAL_TTL_INVALID")
    try:
        issued = int(_aware(now).timestamp())
        payload = {
            **_approval_payload(attempt_id, case_id, text, evidence, policy_version),
            "issued_at": issued,
            "expires_at": issued + ttl_s,
        }
        encoded = base64.urlsafe_b64encode(_canonical(payload).encode()).decode().rstrip("=")
        signature = hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
        return encoded + "." + signature
    except (ValueError, TypeError) as exc:
        raise TrustError("APPROVAL_INPUT_INVALID") from exc


def verify_approval(token, secret, attempt_id, case_id, text, evidence, now, policy_version=POLICY):
    key = _key(secret)
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= 8192:
            raise ValueError("Missing approval")
        encoded, signature = token.split(".")
        expected = hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid signature")
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        target = _approval_payload(attempt_id, case_id, text, evidence, policy_version)
        if any(payload.get(name) != value for name, value in target.items()):
            raise ValueError("Approval does not match")
        issued, expires = payload["issued_at"], payload["expires_at"]
        if type(issued) is not int or type(expires) is not int:
            raise ValueError("Invalid approval time")
        if not issued <= int(_aware(now).timestamp()) < expires or not 0 < expires - issued <= 120:
            raise ValueError("Expired approval")
    except Exception as exc:
        raise TrustError("APPROVAL_INVALID") from exc
