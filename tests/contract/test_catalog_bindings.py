from __future__ import annotations

import json

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.config import ExternalCatalogConfig
from open_workflow_agent.errors import (
    UnsupportedWorkflowFeature,
    WorkflowSchemaError,
    WorkflowSemanticError,
)
from open_workflow_agent.external_catalog import (
    ExternalCatalogResolver,
    parse_catalog_function_reference,
)
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.workflow import (
    compile_workflow,
    generate_default_workflow,
    resolve_and_compile_workflow,
)


def _workflow():
    workflow = generate_default_workflow()
    workflow["use"] = {"catalogs": {"trusted": {"endpoint": "https://catalog.test/root"}}}
    workflow["do"] = [{"remote": {"call": "echo:1.0.0@trusted"}}]
    return workflow


def _resolver(services, handler):
    policy = ExternalCatalogConfig(allowed_hosts=["catalog.test", "api.test"], cache_ttl_seconds=0)
    resolver = ExternalCatalogResolver(
        {"trusted": policy}, http=HttpClient(transport=httpx.MockTransport(handler))
    )
    services.external_catalogs = resolver
    return resolver


async def _compile(services, resolver, workflow):
    return await resolve_and_compile_workflow(
        workflow, trusted_catalogs=resolver.policies, resolver=resolver, catalog=services.catalog
    )


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_compiled_import_content_is_frozen_and_affects_identity(
    services, engine_name, engine_type
):
    revision = {"value": 1}
    fetched = []

    async def handler(request):
        if request.url.host == "catalog.test":
            fetched.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "call": "http",
                    "with": {
                        "method": "post",
                        "endpoint": "https://api.test/echo",
                        "body": {
                            "revision": revision["value"],
                            "input": "${ . }",
                            "context": "${ $context }",
                        },
                    },
                },
            )
        return httpx.Response(200, json=json.loads(request.content))

    resolver = _resolver(services, handler)
    workflow = _workflow()
    first = await _compile(services, resolver, workflow)
    revision["value"] = 2
    second = await _compile(services, resolver, workflow)
    assert first.source == second.source == workflow
    assert first.fingerprint != second.fingerprint
    assert len(first.imports) == len(second.imports) == 1
    first.imports[0].definition["with"]["body"]["revision"] = "changed"
    assert first.imports[0].definition["with"]["body"]["revision"] == 1
    engine = engine_type()
    await engine.initialize(services)
    try:
        for plan, expected in ((first, 1), (second, 2), (first, 1)):
            handle = services.invocations.create(
                engine=engine_name,
                session_id=None,
                user_id="user",
                workflow_name=plan.name,
                workflow_version=plan.version,
                workflow_fingerprint=plan.fingerprint,
            )
            result = await engine.invoke(await engine.compile(plan), handle, {"input": "${ env }"})
            assert result.status == "completed", result.error
            assert result.output == {
                "revision": expected,
                "input": {"input": "${ env }"},
                "context": {"input": "${ env }"},
            }
    finally:
        await engine.shutdown()
    assert len(fetched) == 2


@pytest.mark.asyncio
async def test_catalog_like_application_data_does_not_fetch_resources(services):
    fetched = []
    resolver = _resolver(services, lambda request: fetched.append(request) or httpx.Response(500))
    workflow = _workflow()
    workflow["do"] = [
        {"data": {"set": {"value": "other:1.0.0@trusted", "call": "other:1.0.0@trusted"}}}
    ]
    await _compile(services, resolver, workflow)
    assert fetched == []


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_frozen_imports_use_the_workflow_expression_mode(services, engine_name, engine_type):
    async def handler(request):
        if request.url.host == "catalog.test":
            return httpx.Response(
                200,
                json={
                    "call": "http",
                    "with": {
                        "method": "post",
                        "endpoint": "https://api.test/echo",
                        "body": {"value": ".value + 1"},
                    },
                },
            )
        return httpx.Response(200, json=json.loads(request.content))

    resolver = _resolver(services, handler)
    workflow = _workflow()
    workflow["evaluate"] = {"mode": "loose"}
    plan = await _compile(services, resolver, workflow)
    engine = engine_type()
    await engine.initialize(services)
    try:
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id="user",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        result = await engine.invoke(plan, handle, {"value": 2})
        assert result.status == "completed", result.error
        assert result.output == {"value": 3}
    finally:
        await engine.shutdown()


@pytest.mark.parametrize(
    "body,kind",
    [
        ("call: http\ncall: http\nwith: {}\n", WorkflowSchemaError),
        (
            "call: http\nwith:\n  method: get\n  endpoint: https://api.test\n  body: .nan\n",
            WorkflowSchemaError,
        ),
        (
            "call: http\nwith:\n  method: get\n  endpoint: https://api.test\n"
            "output:\n  schema:\n    format: json\n    document:\n      $ref: '#/$defs/missing'\n",
            WorkflowSemanticError,
        ),
        ("call: http\nwith:\n  endpoint: https://api.test\n", WorkflowSchemaError),
    ],
)
@pytest.mark.asyncio
async def test_remote_definitions_use_safe_loading_and_fail_closed(services, body, kind):
    resolver = _resolver(services, lambda _: httpx.Response(200, text=body))
    with pytest.raises(kind):
        await _compile(services, resolver, _workflow())


@pytest.mark.parametrize("version", ["1.2.3-alpha.2", "1.2.3+build.001", "1.2.3-alpha+build"])
def test_catalog_versions_support_exact_semantic_pins(version):
    parsed = parse_catalog_function_reference(f"echo:{version}@trusted")
    assert parsed is not None and parsed.version == version


@pytest.mark.parametrize("version", ["01.2.3", "1.2.3-01", "latest", "^1.2.3", "1.2.3/../evil"])
def test_catalog_version_ranges_and_invalid_pins_fail_closed(version):
    assert parse_catalog_function_reference(f"echo:{version}@trusted") is None


@pytest.mark.asyncio
async def test_captured_content_rechecks_changed_deployment_endpoint_policy(services):
    async def handler(request):
        assert request.url.host == "catalog.test"
        return httpx.Response(
            200,
            json={"call": "http", "with": {"method": "get", "endpoint": "https://api.test/result"}},
        )

    resolver = _resolver(services, handler)
    resolver.policies["trusted"].allowed_endpoints = ["https://catalog.test/root"]
    plan = await _compile(services, resolver, _workflow())
    resolver.policies["trusted"].allowed_endpoints = ["https://catalog.test/different"]
    with pytest.raises(UnsupportedWorkflowFeature, match="no longer deployment-approved"):
        await resolver.invoke_frozen(plan.imports[0], {}, variables={})


async def _invoke_plan(services, engine_name, engine_type, plan, data):
    engine = engine_type()
    await engine.initialize(services)
    try:
        handle = services.invocations.create(
            engine=engine_name,
            session_id=None,
            user_id="user",
            workflow_name=plan.name,
            workflow_version=plan.version,
            workflow_fingerprint=plan.fingerprint,
        )
        return await engine.invoke(await engine.compile(plan), handle, data)
    finally:
        await engine.shutdown()


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_imported_tasks_use_the_common_pipeline_and_descriptors(
    services, engine_name, engine_type
):
    definition = {
        "call": "http",
        "input": {
            "schema": {"format": "json", "document": {"type": "object", "required": ["value"]}},
            "from": "${ .value + 1 }",
        },
        "with": {
            "method": "post",
            "endpoint": "https://api.test/echo",
            "body": {
                "value": "${ . * 2 }",
                "raw": "${ $task.input }",
                "reference": "${ $task.reference }",
                "definition": "${ $task.definition }",
                "workflow": "${ $workflow.definition }",
            },
        },
        "output": {"as": "${ . + {finished: true} }"},
        "export": {"as": "${ {saved: $output.value} }"},
        "timeout": "bounded",
        "metadata": {"description": "imported echo"},
    }
    effects = []

    async def handler(request):
        if request.url.host == "catalog.test":
            return httpx.Response(200, json=definition)
        effects.append(request)
        return httpx.Response(200, json=json.loads(request.content))

    resolver = _resolver(services, handler)
    workflow = _workflow()
    workflow["use"]["timeouts"] = {"bounded": {"after": {"seconds": 1}}}
    workflow["do"][0]["remote"].update(
        {
            "input": {"from": "${ .start }"},
            "with": {"value": "${ . + 1 }"},
            "output": {"as": "${ . + {context: $context} }"},
        }
    )
    plan = await _compile(services, resolver, workflow)
    assert plan.imports[0].definition["timeout"] == "bounded"
    assert plan.imports[0].execution_definition["timeout"] == {"after": {"seconds": 1}}
    result = await _invoke_plan(services, engine_name, engine_type, plan, {"start": 2})
    assert result.status == "completed", result.error
    assert result.output == {
        "value": 8,
        "raw": {"value": 3},
        "reference": "/do/0/remote",
        "definition": definition,
        "workflow": workflow,
        "finished": True,
        "context": {"saved": 8},
    }
    assert len(effects) == 1
    scopes = [scope for scope in plan.scopes if scope.reference == "/do/0/remote/call"]
    assert len(scopes) == 1
    assert scopes[0].tasks[0].reference == "/do/0/remote"
    imported_events = [
        event
        for event in services.events.events
        if event.event_type == "TaskCompleted" and event.task_name == "echo:1.0.0@trusted"
    ]
    assert len(imported_events) == 1
    assert imported_events[0].task_reference == "/do/0/remote"
    assert imported_events[0].operation_id.endswith("/do/0/remote/call/echo:1.0.0@trusted")


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize(
    "case", ["skip", "invalid-input", "invalid-output", "end", "exit", "timeout"]
)
@pytest.mark.asyncio
async def test_imported_task_controls_share_inline_semantics(
    services, engine_name, engine_type, case
):
    import asyncio

    effects = []
    definition = {
        "call": "http",
        "with": {"method": "get", "endpoint": "https://api.test/echo"},
    }
    if case == "skip":
        definition.update({"if": "${ false }", "output": {"as": '${ error("must not run") }'}})
    elif case == "invalid-input":
        definition["input"] = {"schema": {"format": "json", "document": {"type": "string"}}}
    elif case == "invalid-output":
        definition["output"] = {"schema": {"format": "json", "document": {"type": "string"}}}
    elif case in {"end", "exit"}:
        definition["then"] = case
    elif case == "timeout":
        definition["timeout"] = {"after": {"milliseconds": 10}}

    async def handler(request):
        if request.url.host == "catalog.test":
            return httpx.Response(200, json=definition)
        effects.append(request)
        if case == "timeout":
            await asyncio.sleep(1)
        return httpx.Response(200, json={"echo": True})

    resolver = _resolver(services, handler)
    workflow = _workflow()
    workflow["do"].append({"after": {"set": {"after": True, "input": "${ . }"}}})
    plan = await _compile(services, resolver, workflow)
    result = await _invoke_plan(services, engine_name, engine_type, plan, {"original": True})
    if case in {"invalid-input", "invalid-output", "timeout"}:
        assert result.status == "faulted", result
        assert result.error["details"]["instance"] == "/do/0/remote"
    else:
        assert result.status == "completed", result.error
        if case == "end":
            assert result.output == {"echo": True}
        else:
            expected = {"original": True} if case == "skip" else {"echo": True}
            assert result.output == {"after": True, "input": expected}
    assert len(effects) == (0 if case in {"skip", "invalid-input"} else 1)


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("transition", ["continue", "exit", "end"])
@pytest.mark.asyncio
async def test_legacy_catalog_registration_preserves_exports_and_control_flow(
    services, engine_name, engine_type, transition
):
    async def handler(request):
        if request.url.host == "catalog.test":
            return httpx.Response(
                200,
                json={
                    "call": "http",
                    "with": {"method": "get", "endpoint": "https://api.test/echo"},
                    "export": {"as": "${ {saved: $output.value} }"},
                    "then": transition,
                },
            )
        return httpx.Response(200, json={"value": 7})

    resolver = _resolver(services, handler)
    workflow = _workflow()
    workflow["do"].append({"after": {"set": "${ $context }"}})
    plan = compile_workflow(workflow, trusted_catalogs=resolver.policies)
    assert not plan.imports
    await resolver.resolve_workflow(workflow, services.catalog)
    result = await _invoke_plan(services, engine_name, engine_type, plan, {})
    assert result.status == "completed", result.error
    assert result.output == ({"value": 7} if transition == "end" else {"saved": 7})


@pytest.mark.asyncio
async def test_imported_named_timeout_must_resolve_before_execution(services):
    effects = []

    async def handler(request):
        if request.url.host == "catalog.test":
            return httpx.Response(
                200,
                json={
                    "call": "http",
                    "with": {"method": "get", "endpoint": "https://api.test/echo"},
                    "timeout": "missing",
                },
            )
        effects.append(request)
        return httpx.Response(200)

    resolver = _resolver(services, handler)
    with pytest.raises(WorkflowSemanticError) as error:
        await _compile(services, resolver, _workflow())
    assert error.value.details["reference"] == "/timeout"
    assert error.value.details["function"] == "echo:1.0.0@trusted"
    assert effects == []
