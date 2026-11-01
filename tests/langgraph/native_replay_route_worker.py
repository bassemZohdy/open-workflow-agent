"""Isolated worker that posts a public replay request and is interrupted mid-stage.

Used only by the process-interruption tests of the public replay route. It boots the real
app over the shared database, patches the coordinator's phase hook so the process stops at
the requested stage, and prints a marker the parent waits for before killing it.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
from native_state_test_support import configuration
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import NativeStateReplayConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


async def main():
    root, source_id, key, reference, policy, phase = sys.argv[1:]
    config = configuration()
    config.execution_authorization.policy.rules[0].actions.append("state.replay")
    config.native_state_replay = NativeStateReplayConfig()
    datasource = os.getenv("OWA_TEST_POSTGRES_URL")
    if datasource:
        config.persistence.datasource = datasource

    def interrupted_effect(_prompt):
        print("CRASH_PHASE effect " + bound_target["id"], flush=True)
        time.sleep(60)
        return {"response": "unexpected"}

    bound_target = {"id": ""}
    model = FakeModel(response=interrupted_effect if phase == "effect" else None)
    services = RuntimeServices(
        config, model=model, database_root=None if datasource else Path(root)
    )
    engine = LangGraphWorkflowEngine()
    engine._execution_ownership_seconds = 12 if phase == "effect" else 2
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    controller = services.native_replay_controller
    coordinator = controller.coordinator
    real = coordinator.run

    async def interrupted(*args, **kwargs):
        original = kwargs["_phase"]

        def hook(name, target):
            if name == "bound":
                bound_target["id"] = target
            if name == phase:
                print("CRASH_PHASE " + name + " " + target, flush=True)
                time.sleep(60)
            original(name, target)

        kwargs["_phase"] = hook
        return await real(*args, **kwargs)

    coordinator.run = interrupted
    app = create_app(config=config, services=services, engine=engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer replay-state-alice"},
    ) as client:
        await client.post(
            f"/v1/invocations/{source_id}/state-replays",
            json={"reference": reference, "acknowledge_reexecution": policy == "reexecute"},
            headers={"Idempotency-Key": key},
        )
        # The route answers after binding; an effect-phase stop happens in the background.
        await asyncio.sleep(60)
    raise RuntimeError("requested crash point was not reached")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
