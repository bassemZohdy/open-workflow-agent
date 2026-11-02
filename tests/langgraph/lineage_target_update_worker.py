"""Pause a public lineage-target update after its native commit, before acknowledgement."""

import asyncio
import json
import sys
from pathlib import Path

import httpx
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.native_state_transition import AttestedStateControls


async def main():
    settings = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    config = RuntimeConfig.model_validate(settings["config"])
    services = RuntimeServices(
        config,
        model=FakeModel(),
        database_root=None if config.persistence.datasource else Path(settings["root"]),
    )
    engine = LangGraphWorkflowEngine()
    engine._execution_ownership_seconds = settings["ownership_seconds"]
    apply = AttestedStateControls.apply

    async def stop_after_commit(self, request, handle):
        proof = await apply(self, request, handle)
        print(
            "TARGET_UPDATE_COMMITTED " + request.identity.key + " " + request.request_digest,
            flush=True,
        )
        await asyncio.Event().wait()
        return proof

    AttestedStateControls.apply = stop_after_commit
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(config=config, services=services, engine=engine)
            ),
            base_url="http://test",
            headers={"Authorization": "Bearer replay-state-alice"},
        ) as client:
            response = await client.post(
                f"/v1/invocations/{settings['target']}/state-updates",
                json=settings["body"],
                headers={"Idempotency-Key": settings.get("key", "target-update-19")},
            )
            raise RuntimeError(f"native commit stop was not reached: {response.status_code}")
    finally:
        await engine.shutdown()
        services.close()


with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    runner.run(main())
