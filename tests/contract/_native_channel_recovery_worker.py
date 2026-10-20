"""Producer/recovery worker for the declared LangGraph channel extension."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

from _process_recovery_worker import write_json
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def plan():
    return compile_workflow(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "test",
                "name": "native-channel-process-recovery",
                "version": "1.0.0",
                "metadata": {
                    "owa": {
                        "nativeExtensions": [{"name": "langgraph.channels", "version": "1.0.0"}],
                        "nativeExtensionData": {
                            "langgraph.channels@1.0.0": {
                                "channels": [
                                    {
                                        "id": "items",
                                        "reducer": "append",
                                        "initial": ["seed"],
                                        "writes": [{"task": "/do/0"}, {"task": "/do/2"}],
                                    }
                                ],
                                "reads": [
                                    {
                                        "task": "/do/3",
                                        "as": "owa_native_channels",
                                        "channels": ["items"],
                                    }
                                ],
                            }
                        },
                    }
                },
            },
            "do": [
                {"write_first": {"set": '${ ["first"] }'}},
                {"pause": {"wait": {"seconds": 300}}},
                {"write_second": {"set": '${ ["second"] }'}},
                {"read_items": {"set": {"items": "${ $owa_native_channels.items }"}}},
            ],
        }
    )


async def run(directory: Path, mode: str) -> None:
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if scenario["backend"] == "postgresql" else None
    config = (
        RuntimeConfig.model_validate({"persistence": {"datasource": datasource}})
        if datasource
        else RuntimeConfig()
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else directory
    )
    module = importlib.import_module("open_workflow_agent_langgraph")
    engine = module.LangGraphWorkflowEngine()
    await engine.initialize(services)
    if not engine.native.task_nodes_available:
        raise RuntimeError("native LangGraph dependency is required")
    await engine._timer_dispatcher.stop()
    workflow = plan()
    try:
        if mode == "start":
            handle = services.invocations.create(
                engine="langgraph",
                session_id="native-channel-process-recovery",
                user_id="native-channel-test",
                workflow_name=workflow.name,
                workflow_version=workflow.version,
                workflow_fingerprint=workflow.fingerprint,
            )
            result = await engine.invoke(workflow, handle, {"origin": "original"})
            if result.status != "waiting" or handle.status != "waiting":
                raise RuntimeError("producer did not reach durable channel wait")

            from open_workflow_agent.workflow import ExecutionState
            from open_workflow_agent_langgraph.graph import LangGraphScopeExecutor

            async def inspect_native_checkpoint(saver):
                driver = LangGraphScopeExecutor(workflow, saver, handle.invocation_id)
                graph = driver.compile_scope(
                    workflow.scopes[0], engine.executor, ExecutionState(data={})
                )
                return await graph.aget_state(driver.checkpoint_config("/do", 1))

            saved = await engine.native._with_saver(inspect_native_checkpoint)
            channel_values = saved.values.get("native_channels")
            if channel_values != {"items": ["seed", "first"]}:
                raise RuntimeError(
                    "committed first reducer delta is absent or duplicated: "
                    f"keys={sorted(saved.values)} values={channel_values!r}"
                )
            write_json(
                directory / "ready.json",
                {
                    "pid": os.getpid(),
                    "invocation_id": handle.invocation_id,
                    "backend": scenario["backend"],
                    "status": handle.status,
                    "channel_values": channel_values,
                },
            )
            while not (directory / "ack.json").exists():
                await asyncio.sleep(0.01)
            write_json(directory / "acknowledged.json", {"pid": os.getpid()})
            await asyncio.Event().wait()

        marker = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
        handle = services.invocations.get(marker["invocation_id"])
        if handle is None or handle.status != "waiting":
            raise RuntimeError("waiting invocation is unavailable after producer stop")
        result = await asyncio.wait_for(
            engine.resume(handle, {"resume": True}, workflow), timeout=30
        )
        expected = {"items": ["seed", "first", "second"]}
        if result.status != "completed" or result.output != expected:
            raise RuntimeError("channel delta was lost or replayed during recovery")
        duplicate = await engine.resume(handle, {"late": True}, workflow)
        if duplicate != result:
            raise RuntimeError("terminal duplicate resume changed the channel result")
        write_json(
            directory / "result.json",
            {
                "pid": os.getpid(),
                "producer_pid": marker["pid"],
                "status": result.status,
                "output": result.output,
                "duplicate_output": duplicate.output,
            },
        )
    finally:
        await engine.shutdown()
        services.close()
        if mode == "start":
            write_json(directory / "start-cleaned.json", {"pid": os.getpid()})


if __name__ == "__main__":
    location, selected_mode = Path(sys.argv[1]), sys.argv[2]
    try:
        asyncio.run(run(location, selected_mode))
    except BaseException as error:
        write_json(
            location / (selected_mode + "-failure.json"),
            {"exception": type(error).__name__, "message": str(error)},
        )
        raise
