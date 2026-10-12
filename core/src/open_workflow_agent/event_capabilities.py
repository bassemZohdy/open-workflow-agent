"""Qualification gate for the embedded generic event delivery profile."""

from __future__ import annotations

from typing import Any

from .events import EventBus

# Enabled only after the complete S2-05 acceptance record qualifies the profile.
EMBEDDED_EVENT_PROFILE_QUALIFIED = False


def event_capabilities(bus: EventBus) -> dict[str, Any]:
    return {
        "emit": True,
        "listen": True,
        "durable": bool(bus.durable and EMBEDDED_EVENT_PROFILE_QUALIFIED),
    }
