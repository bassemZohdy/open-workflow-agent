"""Fresh runtime startup and HTTP recovery over the configured public profile."""

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


async def main():
    value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    config = RuntimeConfig.model_validate(value["config"])
    services = RuntimeServices(config, model=FakeModel(), database_root=value["root"])
    engine = LangGraphWorkflowEngine()
    try:
        await engine.initialize(services)
        await engine._timer_dispatcher.stop()
        app = create_app(config=config, services=services, engine=engine)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer controlled-state-alice"},
        ) as client:
            path = "/v1/invocations/" + value["invocation_id"]
            current = await client.get(path + "/state")
            assert current.status_code == 200, current.text
            repeated = await client.post(
                path + "/state-updates",
                json=value["command"],
                headers={"Idempotency-Key": "update-1"},
            )
            assert repeated.status_code == 200, repeated.text
            resumed = await client.post(path + "/resume", json={"input": None})
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["output"] == {"value": 8}, resumed.text
            Path(sys.argv[2]).write_text(
                json.dumps(
                    {
                        "data": current.json()["items"][0]["data"],
                        "update": repeated.json(),
                        "output": resumed.json()["output"],
                        "model_calls": len(services.model.calls),
                        "bindings": len(engine.state_controller.bindings),
                    }
                ),
                encoding="utf-8",
            )
    finally:
        await engine.shutdown()
        services.close()


with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    runner.run(main())
