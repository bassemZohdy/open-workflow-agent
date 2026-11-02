"""Boot the public branch route and stop at a requested coordinator stage."""

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
from native_state_test_support import configuration
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import NativeStateBranchConfig, NativeStateUpdateConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


async def main():
    root, source_id, key, reference, policy, phase = sys.argv[1:]
    config = configuration(timeout_seconds=30)
    config.execution_authorization.policy.rules[0].actions.extend(
        ["state.branch", "state.update", "invocations.cancel"]
    )
    config.native_state_updates = NativeStateUpdateConfig()
    config.native_state_branch = NativeStateBranchConfig()
    datasource = os.getenv("OWA_TEST_POSTGRES_URL")
    if datasource:
        config.persistence.datasource = datasource
    bound = {"id": ""}

    def held_effect(_prompt):
        print("BRANCH_ROUTE_PHASE effect " + bound["id"], flush=True)
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
    coordinator = services.native_branch_controller.coordinator
    run = coordinator.run_branch

    async def interrupted(*args, **kwargs):
        original = kwargs["_phase"]

        def hook(name, target):
            if name == "bound":
                bound["id"] = target
            if name == phase:
                print("BRANCH_ROUTE_PHASE " + name + " " + target, flush=True)
                time.sleep(60)
            original(name, target)

        kwargs["_phase"] = hook
        return await run(*args, **kwargs)

    coordinator.run_branch = interrupted
    app = create_app(config=config, services=services, engine=engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer replay-state-alice"},
    ) as client:
        await client.post(
            f"/v1/invocations/{source_id}/state-branches",
            json={
                "reference": reference,
                "task_reference": "/do/1/second",
                "values": {"value": 7},
                "acknowledge_reexecution": policy == "reexecute",
            },
            headers={"Idempotency-Key": key},
        )
        await asyncio.sleep(60)
    raise RuntimeError("requested branch stop point was not reached")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
