from __future__ import annotations

import json

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.external_catalog import ExternalCatalogResolver
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_langgraph import LangGraphWorkflowEngine

FUNCTION_YAML = """\
call: http
with:
  method: post
  endpoint: https://api.test/echo
  body:
    value: ${ .value }
"""


WORKFLOW = {
    "document": {
        "dsl": "1.0.3",
        "namespace": "catalog-contract",
        "name": "external-function",
        "version": "1.0.0",
    },
    "use": {"catalogs": {"trusted": {"endpoint": {"uri": "https://catalog.test/root"}}}},
    "do": [{"remote": {"call": "echo:1.0.0@trusted"}}],
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_name", "engine_type"),
    [("adk", AdkWorkflowEngine), ("langgraph", LangGraphWorkflowEngine)],
)
async def test_external_catalog_has_identical_engine_contract(tmp_path, engine_name, engine_type):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/root/functions/echo/1.0.0/function.yaml":
            return httpx.Response(200, text=FUNCTION_YAML)
        return httpx.Response(200, json={"echo": json.loads(request.content)["value"]})

    config = RuntimeConfig(
        workflow={"external_catalogs": {"trusted": {"allowed_hosts": ["catalog.test", "api.test"]}}}
    )
    services = RuntimeServices(config, model=FakeModel(), database_root=tmp_path / engine_name)
    services.external_catalogs = ExternalCatalogResolver(
        {"trusted": config.workflow.external_catalogs["trusted"]},
        http=HttpClient(transport=httpx.MockTransport(handler)),
    )
    engine = engine_type()
    try:
        await engine.initialize(services)
        plan = compile_workflow(WORKFLOW, trusted_catalogs=config.workflow.external_catalogs)
        await services.external_catalogs.resolve_workflow(plan.source, services.catalog)
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        result = await engine.invoke(plan, handle, {"value": "same"})
        assert result.status == "completed"
        assert result.output == {"echo": "same"}
    finally:
        await engine.shutdown()
        services.close()
