"""Compatibility import for the canonical collection delivery gate."""

from app.modules.conversations.dispatch import dispatch, simulate_dispatch

__all__ = ["dispatch", "simulate_dispatch"]
