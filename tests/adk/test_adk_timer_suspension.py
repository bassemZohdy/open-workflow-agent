"""Public ADK RequestInput releases runners and preserves receipt admission."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, TypedDict

import pytest
from open_workflow_agent.effects import EffectIdentity, EffectJournal, effect_request_digest
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.timers import TimerSuspension
from open_workflow_agent_adk.native import ADK_AVAILABLE, AdkRecoveryContext, NativeAdkRunner

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not ADK_AVAILABLE, reason="native ADK dependency is absent"),
]


class NativeArguments(TypedDict):
    session_id: str
    user_id: str | None
    invocation_id: str
    database_path: str
    _recovery_context: AdkRecoveryContext


@pytest.mark.parametrize(
    "corruption",
    [
        None,
        "missing-receipt",
        "changed-due",
        "wrong-wake",
        "corrupt-schema",
        "known-wake-map",
        "map-changed-due",
        "map-wrong-key",
        "future-only-map",
    ],
)
async def test_native_timer_request_reopens_with_strict_common_evidence(
    tmp_path: Path, corruption: str | None
) -> None:
    journal = EffectJournal(tmp_path / "common.sqlite3")
    native = NativeAdkRunner()
    database = str(tmp_path / "adk.sqlite3")
    identity = EffectIdentity(
        "timer-invocation", "fingerprint", "/do/1/pause", (("/do/1/pause", 1),), "wait:arm:v1"
    )
    suspension = TimerSuspension(identity, 1000.0)
    metadata: dict[str, Any] = {}
    context = AdkRecoveryContext(
        identity.invocation_id,
        identity.workflow_fingerprint,
        frozenset({identity.task_reference}),
        journal,
        metadata,
    )
    calls: list[str] = []

    async def runner(value: Any) -> Any:
        assert value == {"original": True}
        calls.append("run")
        if "_timer_wake" in metadata or identity.key in metadata.get("_timer_wakes", {}):
            return {"done": True}
        journal.begin(identity, effect_request_digest("timer-test", {}), "fixture")
        receipt = journal.finish(identity, "fixture", output={"due_at": suspension.due_at})
        await metadata["_effect_receipt_observer"](identity, receipt)
        await metadata["_waiting_checkpoint_observer"]()
        raise suspension

    common = NativeArguments(
        session_id="timer-session",
        user_id=None,
        invocation_id="native-timer-execution",
        database_path=database,
        _recovery_context=context,
    )
    try:
        assert not await native.scheduled_state_exists({"original": True}, **common)
        with pytest.raises(TimerSuspension):
            await native.invoke(runner, {"original": True}, **common)
        assert calls == ["run"]
        assert await native.scheduled_state_exists({"original": True}, **common)
        assert not [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        wake = {
            "timer_key": suspension.timer_key,
            "due_at": suspension.due_at,
            "reason": "timer_due",
        }
        if corruption == "missing-receipt":
            journal.connection.execute(
                "DELETE FROM workflow_effects WHERE identity = ?", (identity.key,)
            )
            journal.connection.commit()
        elif corruption == "changed-due":
            wake["due_at"] = 1001.0
        elif corruption == "wrong-wake":
            wake["timer_key"] = "another-timer"
        elif corruption == "corrupt-schema":
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("DROP TABLE events")
                connection.commit()
            with pytest.raises(InvocationStateError, match="schema is incomplete"):
                await native.scheduled_state_exists({"original": True}, **common)
            with closing(sqlite3.connect(database)) as connection:
                assert (
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE name = 'events'"
                    ).fetchone()
                    is None
                )
            assert calls == ["run"]
            return
        if corruption in {"known-wake-map", "map-changed-due", "map-wrong-key", "future-only-map"}:
            if corruption == "map-changed-due":
                wake["due_at"] = 1001.0
            elif corruption == "map-wrong-key":
                wake["timer_key"] = "another-timer"
            elif corruption == "future-only-map":
                wake = {"timer_key": "another-known-timer", "due_at": 999.0, "reason": "timer_due"}
            metadata["_timer_wakes"] = {wake["timer_key"]: wake}
            if corruption == "map-wrong-key":
                metadata["_timer_wakes"] = {identity.key: wake}
        else:
            metadata["_timer_wake"] = wake
        if corruption == "future-only-map":

            async def event_values() -> Any:
                sessions = native._session_service(database)
                try:
                    saved = await sessions.get_session(
                        app_name="open-workflow-agent",
                        user_id="owa-user",
                        session_id="timer-session",
                    )
                    assert saved is not None
                    return [event.model_dump(mode="json") for event in saved.events]
                finally:
                    await native._close_sessions(sessions)

            before = await event_values()
            with pytest.raises(TimerSuspension) as suspended:
                await native.resume(runner, {"original": True}, **common)
            assert suspended.value.timer_key == identity.key
            assert calls == ["run"]
            assert await event_values() == before
        elif corruption not in {None, "known-wake-map"}:
            with pytest.raises(InvocationStateError):
                await native.resume(runner, {"original": True}, **common)
            assert calls == ["run"]
        else:
            result = await native.resume(runner, {"original": True}, **common)
            assert result == {"done": True} and calls == ["run", "run"]
            # Timer function responses do not replace or weaken original input binding.
            await native.admit_resume({"original": True}, **common)
            with pytest.raises(InvocationStateError):
                await native.admit_resume({"original": False}, **common)
            assert calls == ["run", "run"]
    finally:
        journal.close()
