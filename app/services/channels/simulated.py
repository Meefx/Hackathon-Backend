"""Retired legacy channel entry point. Historical trust dispatches stay read-only."""

from app.trust.errors import TrustError


def deliver(conn, actor, attempt, case, text, now):
    raise TrustError("legacy_dispatch_disabled", 409)
