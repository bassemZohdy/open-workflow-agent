"""A native traced producer/recovery pair; producer termination is external."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow


def write(directory: Path, name: str, value: object) -> None:
    temporary = directory / (name + ".pending")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(directory / name)


async def run(directory: Path, mode: str, scenario: dict) -> None:
    name = scenario["engine"]
    if name == "adk":
        from open_workflow_agent_adk import AdkWorkflowEngine

        engine = AdkWorkflowEngine()
        assert engine.native.available
    else:
        from open_workflow_agent_langgraph import LangGraphWorkflowEngine

        engine = LangGraphWorkflowEngine()
        assert engine.native.task_nodes_available
    datasource = (
        os.environ["OWA_TEST_POSTGRES_URL"] if scenario["backend"] == "postgresql" else None
    )
    config = RuntimeConfig.model_validate(
        {
            "persistence": {"datasource": datasource},
            "observability": {
                "history": {"enabled": True},
                "tracing": {"enabled": True, "propagate_http": True},
            },
        }
    )
    services = RuntimeServices(
        config, model=FakeModel(), database_root=None if datasource else directory / "database"
    )

    async def peer(request: httpx.Request) -> httpx.Response:
        with (directory / "business.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "operation": request.headers["idempotency-key"],
                        "traceparent": request.headers["traceparent"],
                    }
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        return httpx.Response(200, json={"ok": True})

    services.protocols = ProtocolServices(HttpClient(transport=httpx.MockTransport(peer)))
    services.tools.protocols = services.protocols
    definition = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "history",
            "name": scenario["name"],
            "version": "1.0.0",
        },
        "do": [
            {
                "once": {
                    "call": "http",
                    "with": {"method": "POST", "endpoint": "https://service.test/once"},
                }
            },
            {"pause": {"wait": {"seconds": 300}}},
            {"done": {"set": {"ok": True}}},
        ],
    }
    if scenario.get("native_send", False):
        assert name == "langgraph"
        definition["document"]["metadata"] = {
            "owa": {"nativeExtensions": [{"name": "langgraph.send", "version": "1.0.0"}]}
        }
        action = definition["do"][0]
        definition["do"][0] = {
            "fan": {"fork": {"branches": [action, {"other": {"set": {"ok": True}}}]}}
        }
    plan = compile_workflow(definition)
    await engine.initialize(services)
    try:
        if mode == "start":
            await asyncio.wait_for(services.recovery_definitions.prepare_profile(name), timeout=60)
            handle = services.invocations.create(
                engine=name,
                user_id=None,
                session_id=None,
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            result = await engine.invoke(
                plan, handle, {"private": "must-not-retain-native-trace-input"}
            )
            assert result.status == "waiting"
        else:
            original = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
            handle = services.invocations.get(original["invocation_id"])
            assert handle is not None and handle.status == "waiting"
            result = await engine.resume(handle, {"ignored": True}, plan)
            assert result.status == "completed" and result.output == {"ok": True}
        events = [
            event.as_dict()
            for event in services.lifecycle_events.snapshot(1000)
            if event.data.get("invocation_id") == handle.invocation_id
        ]
        write(
            directory,
            "ready.json" if mode == "start" else "result.json",
            {
                "pid": os.getpid(),
                "invocation_id": handle.invocation_id,
                "fingerprint": handle.workflow_fingerprint,
                "status": result.status,
                "events": events,
                "spans": [
                    {"trace_id": span.trace_id, "span_id": span.span_id, "kind": span.kind}
                    for span in services.tracing.snapshot()
                ],
            },
        )
        if mode == "start":
            await asyncio.Event().wait()
    finally:
        await engine.shutdown()
        services.close()
        write(directory, mode + "-cleaned.json", {"pid": os.getpid()})


def main() -> None:
    directory, mode = Path(sys.argv[1]), sys.argv[2]
    scenario = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    if (
        sys.platform == "win32"
        and scenario["engine"] == "langgraph"
        and scenario["backend"] == "postgresql"
    ):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(run(directory, mode, scenario))
    except BaseException as error:
        write(
            directory,
            mode + "-failure.json",
            {"type": type(error).__name__, "witness": uuid4().hex},
        )
        raise


if __name__ == "__main__":
    main()
