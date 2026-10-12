"""Explicit workflow delivery for isolated shared-database contract fixtures."""

from __future__ import annotations

import pytest
from open_workflow_agent.timer_dispatcher import TimerDispatcher


@pytest.fixture
def explicit_workflow_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep another fixture's due invocations out of this fixture's fake model.

    Opt-in callers exercise invoke/resume and their own delivery boundaries.
    Timer dispatch and automatic-wait fixtures retain their normal poller.
    """

    async def no_automatic_delivery(_dispatcher: TimerDispatcher) -> None:
        return None

    monkeypatch.setattr(TimerDispatcher, "start", no_automatic_delivery)
