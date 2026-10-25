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


def plan(*, native_command=False, nested_reader=False):
    definition = {
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
    metadata = definition["document"]["metadata"]["owa"]
    if native_command:
        metadata["nativeExtensions"].append({"name": "langgraph.command", "version": "1.0.0"})
    if nested_reader:
        metadata["nativeExtensionData"]["langgraph.channels@1.0.0"]["reads"].append(
            {"task": "/do/1", "as": "owa_native_channels", "channels": ["items"]}
        )
        definition["do"][1]["pause"] = {
            "do": [{"inside": {"wait": {"seconds": 300}}}],
            "output": {"as": "${ {observed: $owa_native_channels.items} }"},
        }
    return compile_workflow(definition)


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
    workflow = plan(
        native_command=scenario.get("native_command", False),
        nested_reader=scenario.get("nested_reader", False),
    )
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
                snapshots = []
                observed_scopes = set()
                for scope in workflow.scopes:
                    async for checkpoint in saver.alist(
                        driver.checkpoint_config(scope.reference, 1)
                    ):
                        observed_scopes.add(scope.reference)
                        values = checkpoint.checkpoint["channel_values"]
                        candidates = [values, values.get("__start__", {})]
                        candidates.extend(value for _, _, value in checkpoint.pending_writes or [])
                        for value in candidates:
                            if isinstance(value, dict) and "snapshot" in value:
                                snapshots.append(json.loads(value["snapshot"]))
                assert snapshots
                assert all("owa_native_channels" not in value["variables"] for value in snapshots)
                return (
                    await graph.aget_state(driver.checkpoint_config("/do", 1)),
                    len(snapshots),
                    len(observed_scopes),
                )

            saved, checked, scopes = await engine.native._with_saver(inspect_native_checkpoint)
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
                    "snapshots_checked": checked,
                    "checkpoint_scopes": scopes,
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
        scenario = json.loads((location / "scenario.json").read_text(encoding="utf-8"))
        if os.name == "nt" and scenario["backend"] == "postgresql":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(run(location, selected_mode))
    except BaseException as error:
        write_json(
            location / (selected_mode + "-failure.json"),
            {"exception": type(error).__name__, "message": str(error)},
        )
        raise
