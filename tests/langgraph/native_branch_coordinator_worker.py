"""Stop a private branch coordinator at a requested durable boundary."""

import asyncio
import os
import sys
import time
from pathlib import Path

from native_stream_test_support import authority, configuration
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.native_replay_coordinator import NativeBranchCoordinator


async def main():
    root, source_id, request_id, reference, policy, phase = sys.argv[1:]
    config = configuration()
    config.observability.native_streams = None
    config.execution_authorization.policy.rules[0].actions.extend(["state.branch", "state.replay"])
    datasource = os.getenv("OWA_TEST_POSTGRES_URL")
    if datasource:
        config.persistence.datasource = datasource
    target = {"id": ""}

    def held_effect(_prompt):
        print("BRANCH_PHASE effect " + target["id"], flush=True)
        time.sleep(60)
        return {"response": "unexpected"}

    services = RuntimeServices(
        config,
        model=FakeModel(response=held_effect if phase == "effect" else None),
        database_root=None if datasource else Path(root),
    )
    engine = LangGraphWorkflowEngine()
    engine._execution_ownership_seconds = 20 if phase == "effect" else 12
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    source = services.invocations.get(source_id)
    assert source is not None

    def stop_at(name, identity):
        if name == "bound":
            target["id"] = identity
        if name == phase:
            print("BRANCH_PHASE " + name + " " + identity, flush=True)
            time.sleep(60)

    await NativeBranchCoordinator(engine, NativeStateConfig(timeout_seconds=30)).run_branch(
        source,
        authority(services, "alice"),
        request_id=request_id,
        source_reference=reference,
        task_reference="/do/2/second",
        values={"value": 7},
        acknowledge_reexecution=policy == "reexecute",
        _phase=stop_at,
    )
    raise RuntimeError("requested branch stop point was not reached")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
