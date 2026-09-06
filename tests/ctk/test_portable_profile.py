from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from engine_cases import engine_cases
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow

FEATURE_ROOT = Path(__file__).parent / "features"


def _load_scenarios() -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for path in sorted(FEATURE_ROOT.glob("*.feature")):
        lines = path.read_text(encoding="utf-8").splitlines()
        name = ""
        blocks: dict[str, Any] = {}
        current: str | None = None
        buffer: list[str] = []
        preceding = ""
        for line in lines:
            if line.startswith("  Scenario: "):
                if name and blocks.get("workflow") is not None:
                    scenarios.append({"name": name, **blocks})
                name = line.removeprefix("  Scenario: ")
                blocks = {}
                current = None
                buffer = []
            elif line.startswith('    """yaml'):
                if "workflow catalog should contain" in preceding:
                    current = "child_workflow"
                elif "workflow with definition" in preceding:
                    current = "workflow"
                elif "workflow input" in preceding:
                    current = "input"
                elif "fault with error" in preceding:
                    current = "fault_error"
                elif "property with value" in preceding:
                    current = "property_value"
                elif "event should be delivered" in preceding:
                    current = "event"
                else:
                    current = "output"
                buffer = []
            elif line.startswith('    """') and current:
                parsed = yaml.safe_load("\n".join(buffer))
                if current == "property_value":
                    path = blocks.pop("_property_value_path")
                    blocks.setdefault("property_values", {})[path] = parsed
                else:
                    blocks[current] = parsed
                current = None
            elif current:
                buffer.append(line[4:] if line.startswith("    ") else line)
            else:
                match = re.match(r"    And (\S+) should run (first|last)", line)
                if match:
                    blocks.setdefault(match.group(2), []).append(match.group(1))
                match = re.match(
                    r"    And workflow output should have a '([^']+)' property "
                    r"containing (\d+) items",
                    line,
                )
                if match:
                    blocks.setdefault("property_counts", {})[match.group(1)] = int(match.group(2))
                match = re.match(r"    And the workflow output should have properties (.+)", line)
                if match:
                    properties = [item.strip().strip("'") for item in match.group(1).split(",")]
                    blocks.setdefault("properties", []).extend(properties)
                match = re.match(
                    r"    And the workflow output should have a '([^']+)' property with value:",
                    line,
                )
                if match:
                    blocks.setdefault("property_values", {})[match.group(1)] = None
                    blocks["_property_value_path"] = match.group(1)
                if line == "    Then the workflow should fault":
                    blocks["fault"] = True
                match = re.match(
                    r"    And the fake model should fail (\d+) time(?:s)? before succeeding",
                    line,
                )
                if match:
                    blocks["model_failures"] = int(match.group(1))
                match = re.match(r"    And the event bus should contain type '([^']+)'", line)
                if match:
                    blocks.setdefault("event_types", []).append(match.group(1))
            preceding = line
        if name and blocks.get("workflow") is not None:
            scenarios.append({"name": name, **blocks})
    return scenarios


SCENARIOS = _load_scenarios()

ENGINE_CASES = engine_cases()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_name", "engine_type"),
    ENGINE_CASES,
    ids=[name for name, _ in ENGINE_CASES],
)
@pytest.mark.parametrize("scenario", [pytest.param(item, id=item["name"]) for item in SCENARIOS])
async def test_upstream_ctk_portable_profile_scenarios(
    engine_name, engine_type, scenario, tmp_path
):
    """Execute the selected upstream Gherkin scenarios through both adapters."""

    config = RuntimeConfig.model_validate({"model": {"provider": "fake"}})
    services = RuntimeServices(
        config,
        model=FakeModel(failures=int(scenario.get("model_failures", 0))),
        database_root=tmp_path,
    )

    async def protocol_fixture(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/error":
            return httpx.Response(503, json={"error": "controlled protocol failure"})
        if request.url.path == "/v2/pet/1":
            return httpx.Response(
                200,
                json={"id": 1, "name": "ctk-pet", "status": "available"},
            )
        if request.url.path == "/v2/pet/2":
            return httpx.Response(200, json={"id": 2, "name": "ctk-pet-2"})
        if request.url.path == "/v2/pet/getPetByName/Milou":
            return httpx.Response(404, json={"error": "pet not found"})
        if request.url.path == "/v2/pet/findByStatus":
            return httpx.Response(
                200,
                json=[{"id": 1, "name": "ctk-pet", "status": "available"}],
            )
        if request.url.path == "/v2/swagger.json":
            request_body = request.content
            if b"petId" in request_body:
                return httpx.Response(
                    200,
                    json={"id": 1, "name": "ctk-pet", "status": "available"},
                )
            return httpx.Response(
                200,
                json=[{"id": 1, "name": "ctk-pet", "status": "available"}],
            )
        return httpx.Response(200, json={"path": request.url.path})

    services.protocols = ProtocolServices(
        HttpClient(transport=httpx.MockTransport(protocol_fixture))
    )
    services.tools.protocols = services.protocols
    engine = engine_type()
    await engine.initialize(services)
    if "child_workflow" in scenario:
        services.workflow_catalog.register(scenario["child_workflow"])
    workflow = scenario["workflow"]
    plan = compile_workflow(workflow)
    handle = services.invocations.create(
        engine=engine.engine_name,
        session_id=None,
        user_id=None,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    if "event" in scenario:
        invocation = asyncio.create_task(engine.invoke(plan, handle, scenario.get("input", {})))
        for _ in range(200):
            if handle.status == "waiting":
                break
            await asyncio.sleep(0.005)
        assert handle.status == "waiting"
        await services.event_bus.publish(scenario["event"], default_source="urn:owa:ctk:test")
        result = await invocation
    else:
        result = await engine.invoke(plan, handle, scenario.get("input", {}))
    if scenario.get("fault") or "fault_error" in scenario:
        assert result.status == "faulted"
        if "fault_error" in scenario:
            assert result.error is not None
            details = result.error.get("details", {})
            assert all(details.get(key) == value for key, value in scenario["fault_error"].items())
        services.close()
        return
    assert result.status == "completed"
    if "output" in scenario:
        assert result.output == scenario["output"]
    for path, expected_count in scenario.get("property_counts", {}).items():
        value: Any = result.output
        for part in path.split("."):
            assert isinstance(value, dict)
            value = value[part]
        assert isinstance(value, list)
        assert len(value) == expected_count
    for path in scenario.get("properties", []):
        value: Any = result.output
        for part in path.split("."):
            assert isinstance(value, dict)
            assert part in value
            value = value[part]
    for path, expected in scenario.get("property_values", {}).items():
        value: Any = result.output
        for part in path.split("."):
            assert isinstance(value, dict)
            value = value[part]
        assert value == expected
    task_names = [
        event.task_name for event in services.events.events if event.event_type == "TaskStarted"
    ]
    for expected in scenario.get("first", []):
        assert task_names[0] == expected
    for expected in scenario.get("last", []):
        assert task_names[-1] == expected
    published = getattr(services.event_bus, "published", [])
    for event_type in scenario.get("event_types", []):
        assert any(event.type == event_type for event in published)
    services.close()
