"""Fresh-process attestation recovery with the real configured native stores."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from native_stream_test_support import authority
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.state_updates import StateUpdateRequest, StateUpdates
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.native_state_preflight import prepare_native_state_update
from open_workflow_agent_langgraph.native_state_transition import AttestedStateControls


async def main() -> None:
    value = json.loads(Path(sys.argv[1]).read_text())
    config = RuntimeConfig.model_validate(value["config"])
    services = RuntimeServices(config, model=FakeModel(), database_root=value["root"])
    engine = LangGraphWorkflowEngine()
    lease = None
    try:
        await engine.initialize(services)
        await engine._timer_dispatcher.stop()
        handle = services.invocations.get(value["invocation_id"])
        plan = await services.recovery_definitions.resolve_inspection(handle)
        bridge = AttestedStateControls(services, plan)
        coordinator = StateUpdates(
            services.effect_journal, services.invocations, services.execution_authorizations
        )
        coordinator.register(bridge.backend())
        request = StateUpdateRequest.from_descriptor(value["request"])
        if value["mode"] != "inspect":
            lease = services.execution_ownership.acquire(handle, seconds=300)
            handle._execution_lease = lease
            actor = authority(services, "alice")
            if value["mode"] == "prepared":
                prepared = await prepare_native_state_update(
                    services,
                    handle,
                    actor,
                    request_id=request.request_id,
                    base_reference=request.base_reference,
                    task_reference=request.task_reference,
                    values=request.values,
                )
                assert bridge.stage(prepared).request == request
            await coordinator.update(handle, request, actor)
        receipt = services.effect_journal.get(request.identity)
        proof = bridge.inspect(request)
        assert receipt.status == "completed" and proof is not None
        Path(sys.argv[2]).write_text(
            json.dumps(
                {
                    "proof": asdict(proof),
                    "model_calls": len(services.model.calls),
                    "staged": len(bridge.proposals),
                }
            )
        )
    finally:
        if lease is not None:
            services.execution_ownership.release(lease)
        await engine.shutdown()
        services.close()


with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    runner.run(main())
