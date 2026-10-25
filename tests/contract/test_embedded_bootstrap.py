from __future__ import annotations

import pytest
import yaml
from engine_cases import engine_cases
from fastapi.testclient import TestClient
from open_workflow_agent.api import create_app
from open_workflow_agent.catalog import FakeModel, LiteLLMModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
def test_missing_bootstrap_and_workflow_run_embedded_default_without_external_provider(
    tmp_path, monkeypatch, engine_name, engine_type
):
    config = RuntimeConfig.from_file(tmp_path / "absent.yaml")
    config.knowledge.path = str(tmp_path / "absent-knowledge")

    async def external_call(*args, **kwargs):
        pytest.fail("the embedded default must not call an external model")

    monkeypatch.setattr(LiteLLMModel, "complete", external_call)
    services = RuntimeServices(config, database_root=tmp_path)
    assert isinstance(services.model, FakeModel)
    assert services.datasource is None
    engine = engine_type()
    with TestClient(create_app(config=config, services=services, engine=engine)) as client:
        assert client.get("/health/ready").status_code == 200
        result = client.post("/v1/invoke", json={"input": {"question": "embedded"}}).json()
        assert result["status"] == "completed", result
        assert result["output"]["response"]["question"] == "embedded"
        invocation_id = result["invocation_id"]
        plan = services.workflow_catalog.resolve_by_name("default-agent")
        assert plan.source == generate_default_workflow()
    restarted = RuntimeServices(config, database_root=tmp_path)
    with TestClient(create_app(config=config, services=restarted, engine=engine_type())) as client:
        stored = client.post(f"/v1/invocations/{invocation_id}/resume", json={"input": None}).json()
        assert stored["status"] == "completed"
        assert stored["output"] == result["output"]
        assert (
            client.post("/v1/invoke", json={"input": {"question": "after-restart"}}).json()[
                "status"
            ]
            == "completed"
        )


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_workflow_file_owns_behavior_and_bootstrap_bindings_do_not_change_fingerprint(
    tmp_path, engine_name, engine_type
):
    workflow = generate_default_workflow()
    workflow["do"] = [{"calculate": {"set": {"value": "${ .value + 1 }"}}}]
    workflow_file = tmp_path / "workflow.yaml"
    workflow_file.write_text(yaml.safe_dump(workflow), encoding="utf-8")
    bootstraps = []
    for provider, name in [("fake", "fake/default"), ("litellm", "openai/gpt-test")]:
        file = tmp_path / (provider + ".yaml")
        file.write_text(
            yaml.safe_dump(
                {
                    "model": {"provider": provider, "name": name},
                    "workflow": {"path": str(workflow_file)},
                }
            ),
            encoding="utf-8",
        )
        bootstraps.append(RuntimeConfig.from_file(file))
    fingerprints = []
    for index, config in enumerate(bootstraps):
        # No provider request is needed to execute this deterministic workflow.
        services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path / str(index))
        engine = engine_type()
        await engine.initialize(services)
        plan = compile_workflow(config.workflow.path)
        fingerprints.append(plan.fingerprint)
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id="bootstrap-test",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        try:
            result = await engine.invoke(plan, handle, {"value": 4})
            assert result.status == "completed", result.error
            assert result.output == {"value": 5}
            assert services.model.calls == []
        finally:
            await engine.shutdown()
            services.close()
    assert fingerprints[0] == fingerprints[1] == compile_workflow(workflow_file).fingerprint
