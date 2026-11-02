"""Stop a real replay process at a selected multi-generation recovery boundary."""

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
from native_state_test_support import configuration
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import (
    NativeStateBranchConfig,
    NativeStateConfig,
    NativeStateReplayConfig,
    NativeStateUpdateConfig,
)
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


async def main():
    root, source_id, key, reference, phase = sys.argv[1:]
    config = configuration(timeout_seconds=30)
    config.execution_authorization.policy.rules[0].actions.extend(
        ["state.replay", "state.branch", "state.update"]
    )
    bounds = NativeStateConfig(max_native_bytes=4_194_304, timeout_seconds=30)
    config.native_state = bounds
    config.native_state_updates = NativeStateUpdateConfig(bounds=bounds)
    config.native_state_replay = NativeStateReplayConfig(bounds=bounds)
    config.native_state_branch = NativeStateBranchConfig(bounds=bounds)
    datasource = os.getenv("OWA_TEST_POSTGRES_URL")
    if datasource:
        config.persistence.datasource = datasource
    bound = {"id": ""}

    def held_effect(_prompt):
        print("GENERATION_PHASE effect " + bound["id"], flush=True)
        time.sleep(120)
        return {"response": "unexpected"}

    services = RuntimeServices(
        config,
        model=FakeModel(response=held_effect if phase == "effect" else None),
        database_root=None if datasource else Path(root),
    )
    engine = LangGraphWorkflowEngine()
    # Multi-generation proof inspection can take longer than a first-generation
    # fork, especially against PostgreSQL on a remote/container transport.
    engine._execution_ownership_seconds = 60
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    coordinator = services.native_replay_controller.coordinator
    run = coordinator.run

    async def interrupted(*args, **kwargs):
        original = kwargs["_phase"]

        def hook(name, target):
            if name == "bound":
                bound["id"] = target
            if name == phase:
                print("GENERATION_PHASE " + name + " " + target, flush=True)
                time.sleep(120)
            original(name, target)

        kwargs["_phase"] = hook
        return await run(*args, **kwargs)

    coordinator.run = interrupted
    app = create_app(config=config, services=services, engine=engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer replay-state-alice"},
    ) as client:
        response = await client.post(
            f"/v1/invocations/{source_id}/state-replays",
            json={"reference": reference, "acknowledge_reexecution": True},
            headers={"Idempotency-Key": key},
        )
        if response.status_code != 202:
            raise RuntimeError(
                f"generation replay rejected: {response.status_code} {response.text}"
            )
        await asyncio.sleep(120)
    raise RuntimeError("requested generation stop point was not reached")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
