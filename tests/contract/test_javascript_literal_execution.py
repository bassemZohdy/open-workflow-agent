"""The exact literal-only JavaScript fixtures also run through Python adapters."""

import json
import os
from pathlib import Path

import pytest
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/javascript-literal-execution.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("name,engine_type", engine_cases())
async def test_same_literal_execution_fixtures_match_python(tmp_path, fixture, name, engine_type):
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    engine = engine_type()
    if os.getenv("OWA_TEST_REQUIRED_ENGINE") == name:
        available = engine.native.available if name == "adk" else engine.native.task_nodes_available
        assert available, "selected literal-fixture SDK must be actual native execution"
    await engine.initialize(services)
    try:
        plan = compile_workflow(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "tests",
                    "name": fixture["id"],
                    "version": "1.0.0",
                },
                "do": fixture["tasks"],
            }
        )
        handle = services.invocations.create(
            engine=name,
            session_id=None,
            user_id=None,
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        result = await engine.invoke(plan, handle, fixture["input"])
        assert result.status == "completed", result.error
        assert result.output == fixture["expected"] and services.model.calls == []
    finally:
        await engine.shutdown()
        services.close()
