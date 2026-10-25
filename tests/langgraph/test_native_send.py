"""Declared bounded Send fan-out preserves common fork ownership and outcomes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from open_workflow_agent.errors import UnsupportedWorkflowFeature, WorkflowExecutionError
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow
from open_workflow_agent_langgraph import LangGraphWorkflowEngine

pytest.importorskip("langgraph.types")
from contract.test_traced_history_process_recovery import run_case
from open_workflow_agent_langgraph import branch_dispatch as dispatch


def plan_for(branches, *, required=True, compete=False, nested=False):
    source = generate_default_workflow()
    source["document"]["metadata"] = {
        "owa": {
            "nativeExtensions": [
                {"name": "langgraph.send", "version": "1.0.0", "required": required}
            ]
        }
    }
    body = [{"fan": {"fork": {"branches": branches, "compete": compete}}}]
    source["do"] = [{"outer": {"do": body}}] if nested else body
    return compile_workflow(source)


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_actual_langgraph_send_native_scopes_recover_in_a_different_process_once(tmp_path, backend):
    run_case(tmp_path, backend, "langgraph", LangGraphWorkflowEngine, native_send=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("nested", [False, True])
async def test_actual_send_packets_launch_ordered_common_branches(
    services, monkeypatch, required, nested
):
    packets = []
    original = dispatch.Send

    def send(node, arg):
        packets.append((node, arg))
        return original(node, arg)

    monkeypatch.setattr(dispatch, "Send", send)
    plan = plan_for(
        [{"left": {"set": {"left": 1}}}, {"right": {"set": {"right": 2}}}],
        required=required,
        nested=nested,
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    try:
        await services.recovery_definitions.prepare_profile("langgraph")
        handle = services.invocations.create(
            engine="langgraph",
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        result = await engine.invoke(plan, handle, {"private": "not-a-send-payload"})
        assert result.status == "completed", result.error
        assert result.output == [{"left": 1}, {"right": 2}]
        assert packets == (
            [("branch", {"branch_index": 0}), ("branch", {"branch_index": 1})] if required else []
        )
        assert services.model.calls == []
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["end", "fault"])
async def test_common_end_and_fault_drain_native_dispatched_siblings(services, outcome):
    first = (
        {"winner": {"set": {"ended": True}, "then": "end"}}
        if outcome == "end"
        else {
            "bad": {
                "raise": {
                    "error": {
                        "status": 400,
                        "type": "https://errors.test/fault",
                        "title": "controlled",
                    }
                }
            }
        }
    )
    plan = plan_for([first, {"waiting": {"wait": {"seconds": 300}}}])
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    try:
        await services.recovery_definitions.prepare_profile("langgraph")
        handle = services.invocations.create(
            engine="langgraph",
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        result = await asyncio.wait_for(engine.invoke(plan, handle, {}), timeout=20)
        assert result.status == ("completed" if outcome == "end" else "faulted"), result.error
        if outcome == "end":
            assert result.output == {"ended": True}
        else:
            assert result.error["message"] == "controlled"
        assert services.model.calls == []
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("compete,count", [(True, 2), (False, 65)])
async def test_unsupported_send_scope_rejects_before_invocation_or_native_mutation(
    services, compete, count
):
    plan = plan_for([{f"b{i}": {"set": {"ok": i}}} for i in range(count)], compete=compete)
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    path = Path(engine.native.database_path)
    try:
        before = list(services.invocations.connection.raw.iterdump())
        with pytest.raises(UnsupportedWorkflowFeature, match="non-competing"):
            await engine.compile(plan)
        assert list(services.invocations.connection.raw.iterdump()) == before
        assert not path.exists() and services.model.calls == []
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["bound", "startup", "cancel"])
async def test_native_dispatch_cleanup_closes_unstarted_or_cancels_started_coroutines(
    monkeypatch, failure
):
    started = asyncio.Event()
    stopped = []

    async def branch(index):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            stopped.append(index)

    branches = [branch(index) for index in range(65 if failure == "bound" else 2)]
    if failure == "startup":
        monkeypatch.setattr(
            dispatch,
            "Send",
            lambda node, arg: (_ for _ in ()).throw(RuntimeError("PRIVATE-native-error")),
        )
    if failure == "cancel":

        async def run():
            async with dispatch.dispatch_with_native_send(branches):
                await asyncio.Event().wait()

        task = asyncio.create_task(run())
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sorted(stopped) == [0, 1]
    else:
        with pytest.raises(WorkflowExecutionError, match="native branch dispatch") as caught:
            async with dispatch.dispatch_with_native_send(branches):
                pytest.fail("unqualified dispatch must not enter its body")
        assert "PRIVATE" not in str(caught.value)
        assert not stopped
    assert all(branch.cr_frame is None for branch in branches)
