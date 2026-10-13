"""SDK timer interrupts retain native receipt admission and release execution."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph.graph")

from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import effect_request_digest
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timers import TimerSuspension
from open_workflow_agent.workflow import ExecutionState, compile_workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor
from open_workflow_agent_langgraph.native import LANGGRAPH_AVAILABLE

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not LANGGRAPH_AVAILABLE, reason="native LangGraph dependency is absent"),
]


@pytest.mark.parametrize(
    "corruption",
    [
        None,
        "missing-receipt",
        "changed-due",
        "wrong-wake",
        "known-wake-map",
        "map-changed-due",
        "map-wrong-key",
        "future-only-map",
    ],
)
async def test_native_timer_interrupt_reopens_with_strict_common_evidence(
    tmp_path: Path, corruption: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "native",
                "name": "timer",
                "version": "1.0.0",
            },
            "do": [
                {"before": {"set": {"value": "once"}}},
                {"pause": {"wait": {"seconds": 60}}},
                {"after": {"set": {"done": True}}},
            ],
        }
    )
    invocation = "native-timer-fixture"
    metadata: dict[str, Any] = {"invocation_id": invocation}
    visits: list[str] = []
    original = engine.executor._run_task
    armed: TimerSuspension | None = None

    async def wait(name: str, definition: Any, state: Any) -> Any:
        nonlocal armed
        visits.append(name)
        if "wait" not in definition:
            return await original(name, definition, state)
        identity = engine.executor._effect_identity(state, "wait:arm:v1")
        journal = services.effect_journal
        journal.begin(identity, effect_request_digest("test-timer", {}), "fixture")
        receipt = journal.finish(identity, "fixture", output={"due_at": 1000.0})
        await engine.executor._notify_receipt(state, identity, receipt)
        armed = TimerSuspension(identity, 1000.0)
        if "_timer_wake" not in state.metadata and identity.key not in state.metadata.get(
            "_timer_wakes", {}
        ):
            raise armed
        return state.data

    monkeypatch.setattr(engine.executor, "_run_task", wait)
    runtime_tasks = set(asyncio.all_tasks())
    try:
        with pytest.raises(TimerSuspension):
            await engine.native.execute_plan(
                engine.executor,
                plan,
                {"original": True},
                metadata=metadata,
                invocation_id=invocation,
            )
        assert armed is not None and visits == ["before", "pause"]
        assert not [
            task for task in asyncio.all_tasks() if task not in runtime_tasks and not task.done()
        ]
        wake = {"timer_key": armed.timer_key, "due_at": armed.due_at, "reason": "timer_due"}
        if corruption == "missing-receipt":
            services.effect_journal.connection.execute(
                "DELETE FROM workflow_effects WHERE identity = ?", (armed.timer_key,)
            )
            services.effect_journal.connection.commit()
        elif corruption == "changed-due":
            wake["due_at"] = 1001.0
        elif corruption == "wrong-wake":
            wake["timer_key"] = "another-timer"
        resume_metadata = {**metadata, "_timer_wake": wake}
        if corruption in {"known-wake-map", "map-changed-due", "map-wrong-key", "future-only-map"}:
            if corruption == "map-changed-due":
                wake["due_at"] = 1001.0
            elif corruption == "map-wrong-key":
                wake["timer_key"] = "another-timer"
            elif corruption == "future-only-map":
                wake = {"timer_key": "another-known-timer", "due_at": 999.0, "reason": "timer_due"}
            resume_metadata = {**metadata, "_timer_wakes": {wake["timer_key"]: wake}}
            if corruption == "map-wrong-key":
                resume_metadata["_timer_wakes"] = {armed.timer_key: wake}
        if corruption == "future-only-map":

            async def checkpoint_value(saver: Any) -> Any:
                driver = LangGraphScopeExecutor(plan, saver, invocation)
                saved = await saver.aget_tuple(driver.checkpoint_config("/do", 1))
                assert saved is not None
                return copy.deepcopy(saved.checkpoint), copy.deepcopy(saved.pending_writes)

            before = await engine.native._with_saver(checkpoint_value)
            with pytest.raises(TimerSuspension) as suspended:
                await engine.native.execute_plan(
                    engine.executor,
                    plan,
                    {"original": True},
                    metadata=resume_metadata,
                    invocation_id=invocation,
                    resume=True,
                )
            assert suspended.value.timer_key == armed.timer_key
            assert visits == ["before", "pause"]
            assert await engine.native._with_saver(checkpoint_value) == before
        elif corruption not in {None, "known-wake-map"}:
            with pytest.raises(InvocationStateError):
                await engine.native.execute_plan(
                    engine.executor,
                    plan,
                    {"original": True},
                    metadata=resume_metadata,
                    invocation_id=invocation,
                    resume=True,
                )
            assert visits == ["before", "pause"]
        else:
            output = await engine.native.execute_plan(
                engine.executor,
                plan,
                {"original": True},
                metadata=resume_metadata,
                invocation_id=invocation,
                resume=True,
            )
            assert output == {"done": True}
            assert visits == ["before", "pause", "pause", "after"]
    finally:
        await engine.shutdown()
        services.close()


async def test_scheduled_native_absence_is_distinct_from_corrupt_checkpoint(tmp_path: Path) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "native",
                "name": "scheduled",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )
    handle = services.invocations.create(
        engine=engine.engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    services.invocations.bind_input(handle, {"original": True})
    try:
        assert not await engine._scheduled_native_state_exists(handle, plan)
        result = await engine.invoke(plan, handle, {"original": True})
        assert result.status == "completed"
        assert await engine._scheduled_native_state_exists(handle, plan)

        async def corrupt(saver: Any) -> None:
            driver = LangGraphScopeExecutor(plan, saver, handle.invocation_id)
            graph = driver.compile_scope(plan.scopes[0], engine.executor, ExecutionState(data={}))
            await graph.aupdate_state(driver.checkpoint_config("/do", 1), {"snapshot": "{}"})

        await engine.native._with_saver(corrupt)
        with pytest.raises(InvocationStateError):
            await engine._scheduled_native_state_exists(handle, plan)
    finally:
        await engine.shutdown()
        services.close()


async def test_scheduled_valid_sdk_prestart_checkpoint_reuses_bound_invocation(
    tmp_path: Path,
) -> None:
    from langgraph.checkpoint.base import empty_checkpoint

    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "native",
                "name": "prestart",
                "version": "1.0.0",
            },
            "schedule": {"after": {"seconds": 2}},
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )
    schedule = services.schedules.create(plan, {"original": True}, now=100)
    claimed = services.schedules.claim_due(
        now=102, owner="prestart-fixture", schedule_id=schedule.schedule_id
    )
    assert claimed is not None
    handle = services.schedules.bind_invocation(claimed, engine=engine.engine_name, now=102)
    try:
        lease = services.execution_ownership.acquire(handle, seconds=60)
        try:
            services.recovery_definitions.admit(
                plan, handle, {"original": True}, execution_lease=lease
            )
        finally:
            services.execution_ownership.release(lease)
        # Capture the SDK's actual prestart serialization. Publishing a copy
        # through the SDK models this exact initial boundary, without assuming
        # that failure of a pipelined write prevents later queued checkpoints.
        await engine.native.execute_plan(
            engine.executor,
            plan,
            {"original": True},
            metadata={"invocation_id": handle.invocation_id},
            invocation_id=handle.invocation_id,
        )

        async def prestart(saver: Any) -> None:
            driver = LangGraphScopeExecutor(plan, saver, handle.invocation_id)
            config = driver.checkpoint_config("/do", 1)
            initial = await anext(
                checkpoint
                async for checkpoint in saver.alist(config)
                if checkpoint.metadata["step"] == -1
            )
            checkpoint = copy.deepcopy(initial.checkpoint)
            checkpoint["id"] = empty_checkpoint()["id"]
            await saver.aput(config, checkpoint, initial.metadata, checkpoint["channel_versions"])

        await engine.native._with_saver(prestart)
        assert not await engine._scheduled_native_state_exists(handle, plan)
        result = await engine.dispatch_scheduled(handle, plan, {"original": True})
        assert result.status == "completed" and result.output == {"done": True}
        assert result.invocation_id == handle.invocation_id
    finally:
        await engine.shutdown()
        services.close()
