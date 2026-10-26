"""A fresh process reads existing native history through the authenticated API."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "core/src"), str(ROOT / "engines/langgraph/src")]


async def run() -> None:
    import httpx
    from open_workflow_agent.api import create_app
    from open_workflow_agent.catalog import FakeModel
    from open_workflow_agent.config import RuntimeConfig
    from open_workflow_agent.services import RuntimeServices
    from open_workflow_agent_langgraph import LangGraphWorkflowEngine

    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    services = RuntimeServices(
        RuntimeConfig.model_validate(request["config"]),
        model=FakeModel(),
        database_root=request["root"],
    )
    engine = LangGraphWorkflowEngine()
    await engine.initialize(services)
    await engine._timer_dispatcher.stop()
    try:
        app = create_app(config=services.config, services=services, engine=engine)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer controlled-state-alice"},
        ) as client:
            path = f"/v1/invocations/{request['invocation_id']}"
            response = await client.get(path + "/state", params={"reference": request["reference"]})
            assert response.status_code == 200, response.text
            history = await client.get(path + "/states")
            assert history.status_code == 200, history.text
            assert not services.model.calls
            Path(sys.argv[2]).write_text(
                json.dumps(
                    {
                        "selected": response.json()["items"][0],
                        "history": [item["reference"] for item in history.json()["items"]],
                        "model_calls": len(services.model.calls),
                    }
                ),
                encoding="utf-8",
            )
    finally:
        await engine.shutdown()
        services.close()


if __name__ == "__main__":
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run())
