from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.trust.errors import TrustError
from app.trust.security import (
    TOKEN_AUDIENCE,
    TOKEN_ISSUER,
    TOKEN_TTL_SECONDS,
    Actor,
    decode_token,
    issue_token,
    require,
    require_tool,
)

NOW = datetime(2026, 11, 7, 5, 0, tzinfo=UTC)
SECRET = "synthetic-test-signing-key-32-bytes-minimum"


def actor(role="operator", principal="cf", product="spaylater"):
    return Actor("synthetic-user", role, principal, product)


def test_jwt_preserves_authenticated_principal_and_product():
    scoped = actor(principal="ldn", product="spinjam")
    token = issue_token(scoped, SECRET, NOW)
    assert decode_token(token, SECRET, NOW) == scoped


def test_jwt_rejects_wrong_key_and_role_tampering():
    token = issue_token(actor(), SECRET, NOW)
    header, _payload, signature = token.split(".")
    altered_claims = jwt.decode(token, options={"verify_signature": False})
    altered_claims["role"] = "compliance"
    forged_payload = jwt.encode(
        altered_claims, "different-synthetic-key-32-bytes-long", algorithm="HS256"
    ).split(".")[1]
    with pytest.raises(TrustError) as exc:
        decode_token(f"{header}.{forged_payload}.{signature}", SECRET, NOW)
    assert exc.value.status == 401
    with pytest.raises(TrustError):
        decode_token(token, "different-synthetic-key-32-bytes-long", NOW)


@pytest.mark.parametrize("elapsed", [TOKEN_TTL_SECONDS, TOKEN_TTL_SECONDS + 1, -1])
def test_jwt_expiry_and_not_before_use_injected_clock(elapsed):
    token = issue_token(actor(), SECRET, NOW)
    with pytest.raises(TrustError) as exc:
        decode_token(token, SECRET, NOW + timedelta(seconds=elapsed))
    assert exc.value.code == "TOKEN_INVALID"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "another-service"},
        {"aud": [TOKEN_AUDIENCE, "another-service"]},
        {"iss": "another-issuer"},
        {"role": "superadmin"},
        {"principal": "cf", "product": "spinjam"},
        {"exp": True},
        {"iat": "1794027600"},
        {"exp": int(NOW.timestamp()) + TOKEN_TTL_SECONDS + 1},
    ],
)
def test_jwt_rejects_invalid_scope_and_claims(overrides):
    claims = {
        "sub": "synthetic-user",
        "role": "operator",
        "principal": "cf",
        "product": "spaylater",
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "iat": int(NOW.timestamp()),
        "nbf": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 60,
    }
    claims.update(overrides)
    token = jwt.encode(claims, SECRET, algorithm="HS256")
    with pytest.raises(TrustError):
        decode_token(token, SECRET, NOW)


def test_jwt_rejects_unsigned_token_and_missing_claims():
    with pytest.raises(TrustError):
        decode_token(jwt.encode({"sub": "synthetic-user"}, "", algorithm="none"), SECRET, NOW)
    with pytest.raises(TrustError):
        decode_token(jwt.encode({"sub": "synthetic-user"}, SECRET, algorithm="HS256"), SECRET, NOW)


def test_secret_and_naive_clock_fail_closed():
    with pytest.raises(TrustError) as exc:
        issue_token(actor(), "short", NOW)
    assert exc.value.status == 503
    with pytest.raises(TrustError):
        issue_token(actor(), SECRET, NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    ("role", "capability"),
    [
        ("operator", "knowledge:approve"),
        ("knowledge", "knowledge:approve"),
        ("qa", "knowledge:approve"),
        ("admin", "knowledge:approve"),
        ("admin", "template:approve"),
        ("manager", "attempt:create"),
        ("personal", "attempt:create"),
        ("process", "conversation:read"),
        ("operator", "unknown:capability"),
    ],
)
def test_roles_cannot_self_approve_or_bypass_dispatch(role, capability):
    with pytest.raises(TrustError) as exc:
        require(actor(role), capability)
    assert exc.value.status == 403


def test_qa_can_suspend_and_compliance_can_approve():
    require(actor("qa"), "safety:pause")
    require(actor("compliance"), "knowledge:approve")
    require(actor("knowledge"), "knowledge:ingest")
    require(actor("operator"), "attempt:create")


def test_tool_allowlist_binds_role_tool_and_purpose():
    require_tool(actor("personal"), "search_knowledge", "borrower_response")
    for tool, purpose in [
        ("search_knowledge", "research"),
        ("ingest_knowledge", "research"),
        ("arbitrary_fetch", "borrower_response"),
        ("send_message", "borrower_response"),
    ]:
        with pytest.raises(TrustError) as exc:
            require_tool(actor("personal"), tool, purpose)
        assert exc.value.code == "TOOL_DENIED"


def test_invalid_actor_scope_and_malformed_capability_fail_closed():
    with pytest.raises(TrustError):
        require(actor(product="spinjam"), "knowledge:read")
    with pytest.raises(TrustError):
        require(actor(), [])
