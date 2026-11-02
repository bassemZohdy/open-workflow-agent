"""Boot and invoke the configuration-only native branch example."""

import os
from pathlib import Path

import httpx
import pytest
import yaml
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from test_state_replay_api import wait_terminal

pytest.importorskip("langgraph.graph")
from open_workflow_agent_langgraph import LangGraphWorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_configuration_only_branch_example(tmp_path, monkeypatch, backend):
    monkeypatch.setenv("OWA_NATIVE_STATE_TOKEN", "controlled-state-owner")
    examples = Path(__file__).resolve().parents[2] / "examples"
    config = RuntimeConfig.model_validate(
        yaml.safe_load((examples / "native-state-branch.yaml").read_text(encoding="utf-8"))
    )
    if backend == "postgresql":
        datasource = os.getenv("OWA_TEST_POSTGRES_URL")
        if not datasource:
            pytest.skip("PostgreSQL datasource required")
        config.persistence.datasource = datasource
    mounted = tmp_path / "native-state-branch-workflow.yaml"
    mounted.write_bytes((examples / mounted.name).read_bytes())
    config.workflow.path = str(mounted)
    config.knowledge.reload.mode = "manual"
    services = RuntimeServices(
        config, model=FakeModel(), database_root=tmp_path if backend == "sqlite" else None
    )
    engine = LangGraphWorkflowEngine()
    app = create_app(config=config, services=services, engine=engine)
    try:
        async with app.router.lifespan_context(app):
            await engine._timer_dispatcher.stop()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer controlled-state-owner"},
            ) as client:
                created = await client.post("/v1/invoke", json={"input": {}})
                assert created.status_code == 200 and created.json()["status"] == "completed", (
                    created.text
                )
                source = created.json()["invocation_id"]
                history = await client.get(f"/v1/invocations/{source}/states")
                assert history.status_code == 200, history.text
                reference = next(
                    item["reference"]
                    for item in history.json()["items"]
                    if item.get("root_task_visits") == {"/do/0/first": 1, "/do/1/second": 1}
                )
                response = await client.post(
                    f"/v1/invocations/{source}/state-branches",
                    json={
                        "reference": reference,
                        "task_reference": "/do/1/second",
                        "values": {"value": 7},
                        "acknowledge_reexecution": True,
                    },
                    headers={"Idempotency-Key": "config-only"},
                )
                assert response.status_code == 202, response.text
                finished = await wait_terminal(services, response.json()["invocation_id"])
                assert finished.status == "completed"
                assert len(services.model.calls) == 2
        assert services.native_branch_controller is None
    finally:
        services.close()
