"""Native child interrupts admit common decisions before checkpoint replay."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("langgraph.graph")

from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.child_suspension import ChildSuspension
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
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
        "missing-registration",
        "changed-registration",
        "unrecorded-wake",
        "wrong-child",
        "changed-deadline",
        "wrong-key",
        "future-only-map",
    ],
)
async def test_native_child_interrupt_replays_only_admitted_common_decision(
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
                "name": "child-interrupt",
                "version": "1.0.0",
            },
            "do": [
                {"before": {"set": {"once": True}}},
                {
                    "child": {
                        "run": {
                            "workflow": {"namespace": "native", "name": "child", "version": "1.0.0"}
                        }
                    }
                },
                {"after": {"set": {"done": True}}},
            ],
        }
    )
    parent = services.invocations.create(
        engine=engine.engine_name,
        session_id=None,
        user_id="correlation",
        owner_principal="principal",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    invocation_id = parent.invocation_id
    services.invocations.bind_input(parent, {})
    parent._execution_lease = services.execution_ownership.acquire(parent, seconds=300)
    child_plan = compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "native",
                "name": "child",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"done": True}}}],
        }
    )
    visits: list[str] = []
    original = engine.executor._run_task
    armed: ChildSuspension | None = None

    async def suspend(name: str, definition: Any, state: Any) -> Any:
        nonlocal armed
        visits.append(name)
        if name != "child":
            return await original(name, definition, state)
        identity = engine.executor._effect_identity(state, "run:workflow")
        admission = replace(identity, operation="policy:child-await:v1")
        if armed is None:
            child = services.children.reserve(identity, parent, child_plan, {})
            registration = services.child_waits.arm(
                identity, parent, child, workflow_source=plan.source, deadline_at=1000
            )
            armed = registration.suspension
        receipt = services.effect_journal.get(admission)
        assert receipt is not None
        await engine.executor._notify_receipt(state, admission, receipt)
        if identity.key not in state.metadata.get("_child_wakes", {}):
            raise armed
        return {"child_result": True}

    monkeypatch.setattr(engine.executor, "_run_task", suspend)
    metadata = {"invocation_id": invocation_id}

    async def checkpoint_value(saver: Any) -> Any:
        driver = LangGraphScopeExecutor(plan, saver, invocation_id)
        saved = await saver.aget_tuple(driver.checkpoint_config("/do", 1))
        assert saved is not None
        return copy.deepcopy(saved.checkpoint), copy.deepcopy(saved.pending_writes)

    try:
        with pytest.raises(ChildSuspension):
            await engine.native.execute_plan(
                engine.executor, plan, {}, metadata=metadata, invocation_id=invocation_id
            )
        assert armed is not None and visits == ["before", "child"]
        # Admission checks the saved wait without requiring a delivery signal.
        await engine.native.admit_resume(
            plan,
            invocation_id,
            {},
            effect_journal=services.effect_journal,
            child_waits=services.child_waits,
        )
        before = await engine.native._with_saver(checkpoint_value)
        with pytest.raises(ChildSuspension):
            await engine.native.execute_plan(
                engine.executor,
                plan,
                {},
                metadata=metadata,
                invocation_id=invocation_id,
                resume=True,
            )
        assert visits == ["before", "child"]
        assert await engine.native._with_saver(checkpoint_value) == before
        child = services.invocations.get(armed.child_id)
        assert child is not None
        services.invocations.update(child, status="completed", output={"child_result": True})
        registration = services.child_waits.registration(armed.identity)
        assert registration is not None
        wake = services.child_waits.wake(registration, parent, now=900)
        assert wake is not None and wake["reason"] == "child_terminal"
        if corruption == "missing-receipt":
            admission = replace(armed.identity, operation="policy:child-await:v1")
            services.effect_journal.connection.execute(
                "DELETE FROM workflow_effects WHERE identity = ?", (admission.key,)
            )
            services.effect_journal.connection.commit()
        elif corruption == "missing-registration":
            services.effect_journal.connection.execute(
                "DELETE FROM child_workflow_waits WHERE run_key = ?", (armed.identity.key,)
            )
            services.effect_journal.connection.commit()
        elif corruption == "changed-registration":
            services.effect_journal.connection.execute(
                "UPDATE child_workflow_waits SET deadline_at = ? WHERE run_key = ?",
                (1001, armed.identity.key),
            )
            services.effect_journal.connection.commit()
        elif corruption == "unrecorded-wake":
            wake_identity = replace(armed.identity, operation="policy:child-wake:v1")
            services.effect_journal.connection.execute(
                "DELETE FROM workflow_effects WHERE identity = ?", (wake_identity.key,)
            )
            services.effect_journal.connection.execute(
                "UPDATE child_workflow_waits SET state = 'pending' WHERE run_key = ?",
                (armed.identity.key,),
            )
            services.effect_journal.connection.commit()
        elif corruption == "wrong-child":
            wake["child_id"] = str(uuid4())
        elif corruption == "changed-deadline":
            wake["deadline_at"] = 1001
        elif corruption in {"wrong-key", "future-only-map"}:
            wake["child_key"] = "another-child"
        resume_metadata = {
            **metadata,
            "_child_wakes": {wake["child_key"]: wake},
        }
        if corruption == "wrong-key":
            resume_metadata["_child_wakes"] = {armed.identity.key: wake}
        if corruption == "future-only-map":
            with pytest.raises(ChildSuspension):
                await engine.native.execute_plan(
                    engine.executor,
                    plan,
                    {},
                    metadata=resume_metadata,
                    invocation_id=invocation_id,
                    resume=True,
                )
            assert await engine.native._with_saver(checkpoint_value) == before
        elif corruption is not None:
            with pytest.raises(InvocationStateError):
                await engine.native.execute_plan(
                    engine.executor,
                    plan,
                    {},
                    metadata=resume_metadata,
                    invocation_id=invocation_id,
                    resume=True,
                )
            assert visits == ["before", "child"]
        else:
            output = await engine.native.execute_plan(
                engine.executor,
                plan,
                {},
                metadata=resume_metadata,
                invocation_id=invocation_id,
                resume=True,
            )
            assert output == {"done": True}
            assert visits == ["before", "child", "child", "after"]
    finally:
        services.execution_ownership.release(parent._execution_lease)
        await engine.shutdown()
        services.close()
