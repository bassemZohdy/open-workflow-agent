"""Isolated replay worker used only by process-interruption integration tests."""

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
from open_workflow_agent_langgraph.native_replay_coordinator import NativeReplayCoordinator


async def main():
    root, source_id, request_id, reference, policy, phase = sys.argv[1:]
    config = configuration()
    config.observability.native_streams = None
    config.execution_authorization.policy.rules[0].actions.append("state.replay")
    datasource = os.getenv("OWA_TEST_POSTGRES_URL")
    if datasource:
        config.persistence.datasource = datasource
    target_for_effect = {"id": ""}

    def interrupted_effect(_prompt):
        print("CRASH_PHASE effect " + target_for_effect["id"], flush=True)
        time.sleep(60)
        return {"response": "unexpected"}

    model = FakeModel(response=interrupted_effect if phase == "effect" else None)
    services = RuntimeServices(
        config, model=model, database_root=None if datasource else Path(root)
    )
    engine = LangGraphWorkflowEngine()
    engine._execution_ownership_seconds = 12 if phase == "effect" else 2
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    source = services.invocations.get(source_id)
    assert source is not None

    def suspend(name, target):
        if name == "bound":
            target_for_effect["id"] = target
        if name == phase:
            print("CRASH_PHASE " + name + " " + target, flush=True)
            time.sleep(60)

    await NativeReplayCoordinator(engine, NativeStateConfig(timeout_seconds=30)).run(
        source,
        authority(services, "alice"),
        request_id=request_id,
        source_reference=reference,
        acknowledge_reexecution=policy == "reexecute",
        _phase=suspend,
    )
    raise RuntimeError("requested crash point was not reached")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
