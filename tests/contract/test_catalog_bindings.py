from __future__ import annotations

import json

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.config import ExternalCatalogConfig
from open_workflow_agent.errors import UnsupportedWorkflowFeature, WorkflowSchemaError
from open_workflow_agent.external_catalog import (
    ExternalCatalogResolver,
    parse_catalog_function_reference,
)
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.workflow import generate_default_workflow, resolve_and_compile_workflow


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
            "output:\n  as: '${ . }'\n",
            UnsupportedWorkflowFeature,
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
