"""Qualification gate for the embedded generic event delivery profile."""

from __future__ import annotations

from typing import Any

from .approvals import ApprovalEventBus
from .durable_events import DurableEventBus
from .events import EventBus

# The complete S2-05 acceptance record qualifies the embedded profile only.
EMBEDDED_EVENT_PROFILE_QUALIFIED = True


def event_capabilities(bus: EventBus) -> dict[str, Any]:
    delegate = bus.delegate if isinstance(bus, ApprovalEventBus) else bus
    return {
        "emit": True,
        "listen": True,
        "durable": bool(
            EMBEDDED_EVENT_PROFILE_QUALIFIED
            and isinstance(delegate, DurableEventBus)
            and delegate.durable
        ),
    }
