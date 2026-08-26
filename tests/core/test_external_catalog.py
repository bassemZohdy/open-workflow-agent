from __future__ import annotations

import hashlib
import json
import socket

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import ExternalCatalogConfig, RuntimeConfig
from open_workflow_agent.errors import ToolError, UnsupportedWorkflowFeature, WorkflowSchemaError
from open_workflow_agent.external_catalog import ExternalCatalogResolver
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import WorkflowExecutor, compile_workflow


def _workflow() -> dict[str, object]:
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "catalog-tests",
            "name": "external",
            "version": "1.0.0",
        },
        "use": {"catalogs": {"trusted": {"endpoint": {"uri": "https://catalog.test/root"}}}},
        "do": [{"remote": {"call": "echo:1.0.0@trusted"}}],
    }


FUNCTION_YAML = """\
call: http
with:
  method: post
  endpoint: https://api.test/echo
  body:
    value: ${ .value }
"""


def _resolver(handler: object, *, pins: dict[str, str] | None = None) -> ExternalCatalogResolver:
    policy = ExternalCatalogConfig(
        allowed_hosts=["catalog.test", "api.test"],
        cache_ttl_seconds=0,
        integrity_pins=pins or {},
    )
    client = HttpClient(
        transport=httpx.MockTransport(handler),
        max_response_bytes=policy.max_response_bytes,
    )
    return ExternalCatalogResolver({"trusted": policy}, http=client)


@pytest.mark.asyncio
async def test_external_catalog_function_is_resolved_and_executed(services):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/root/functions/echo/1.0.0/function.yaml":
            return httpx.Response(200, text=FUNCTION_YAML, headers={"ETag": '"function-v1"'})
        assert request.url.host == "api.test"
        assert request.url.path == "/echo"
        return httpx.Response(200, json={"echo": json.loads(request.content)["value"]})

    resolver = _resolver(handler)
    services.external_catalogs = resolver
    plan = compile_workflow(_workflow(), trusted_catalogs={"trusted": resolver.policies["trusted"]})
    await resolver.resolve_workflow(plan.source, services.catalog)
    result = await WorkflowExecutor(services.catalog, services=services).execute(
        plan, {"value": "hello"}
    )
    assert result == {"echo": "hello"}


def test_external_catalog_requires_deployment_trust():
    with pytest.raises(UnsupportedWorkflowFeature, match="deployment trust"):
        compile_workflow(_workflow())


def test_external_catalog_rejects_inline_authentication():
    workflow = _workflow()
    catalogs = workflow["use"]["catalogs"]  # type: ignore[index]
    catalogs["trusted"]["endpoint"]["authentication"] = {  # type: ignore[index]
        "bearer": "secret"
    }
    with pytest.raises(WorkflowSchemaError, match="schema validation failed"):
        compile_workflow(
            workflow,
            trusted_catalogs={"trusted": ExternalCatalogConfig(allowed_hosts=["catalog.test"])},
        )


@pytest.mark.asyncio
async def test_external_catalog_revalidates_cached_definition_and_checks_integrity(tmp_path):
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, text=FUNCTION_YAML, headers={"ETag": '"function-v1"'})
        assert request.headers["if-none-match"] == '"function-v1"'
        return httpx.Response(304, headers={"ETag": '"function-v1"'})

    digest = hashlib.sha256(FUNCTION_YAML.encode()).hexdigest()
    resolver = _resolver(handler, pins={"echo:1.0.0@trusted": digest})
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        plan = compile_workflow(
            _workflow(), trusted_catalogs={"trusted": resolver.policies["trusted"]}
        )
        await resolver.resolve_workflow(plan.source, services.catalog)
        await resolver.resolve_workflow(plan.source, services.catalog)
        assert len(calls) == 2
    finally:
        services.close()


@pytest.mark.asyncio
async def test_external_catalog_rejects_private_ip_destinations(services):
    workflow = _workflow()
    workflow["use"]["catalogs"]["trusted"]["endpoint"]["uri"] = "https://127.0.0.1/root"  # type: ignore[index]

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("private destination must be rejected before transport")

    policy = ExternalCatalogConfig(allowed_hosts=["127.0.0.1"])
    resolver = ExternalCatalogResolver(
        {"trusted": policy}, http=HttpClient(transport=httpx.MockTransport(handler))
    )
    plan = compile_workflow(workflow, trusted_catalogs={"trusted": policy})
    with pytest.raises(UnsupportedWorkflowFeature, match="disallowed IP"):
        await resolver.resolve_workflow(plan.source, services.catalog)


@pytest.mark.asyncio
async def test_external_catalog_rejects_dns_rebinding_to_private_address(services, monkeypatch):
    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    policy = ExternalCatalogConfig(allowed_hosts=["catalog.test"])
    resolver = ExternalCatalogResolver({"trusted": policy}, http=HttpClient())
    plan = compile_workflow(_workflow(), trusted_catalogs={"trusted": policy})
    with pytest.raises(UnsupportedWorkflowFeature, match="disallowed IP"):
        await resolver.resolve_workflow(plan.source, services.catalog)


@pytest.mark.asyncio
async def test_external_catalog_rejects_redirects_and_oversized_documents(services):
    async def redirect_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://other.test/function.yaml"})

    redirect_resolver = _resolver(redirect_handler)
    redirect_plan = compile_workflow(
        _workflow(), trusted_catalogs={"trusted": redirect_resolver.policies["trusted"]}
    )
    with pytest.raises(ToolError, match="external catalog resolution failed|HTTP request failed"):
        await redirect_resolver.resolve_workflow(redirect_plan.source, services.catalog)

    async def oversized_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 32)

    policy = ExternalCatalogConfig(allowed_hosts=["catalog.test"], max_response_bytes=16)
    oversized_resolver = ExternalCatalogResolver(
        {"trusted": policy},
        http=HttpClient(
            transport=httpx.MockTransport(oversized_handler),
            max_response_bytes=policy.max_response_bytes,
        ),
    )
    oversized_plan = compile_workflow(_workflow(), trusted_catalogs={"trusted": policy})
    with pytest.raises(ToolError, match="maximum size|resolution failed"):
        await oversized_resolver.resolve_workflow(oversized_plan.source, services.catalog)


@pytest.mark.asyncio
async def test_external_catalog_rejects_remote_scripts(services):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="run:\n  script:\n    language: python\n    code: 'return 1'\n"
        )

    resolver = _resolver(handler)
    plan = compile_workflow(_workflow(), trusted_catalogs={"trusted": resolver.policies["trusted"]})
    with pytest.raises(UnsupportedWorkflowFeature, match="script functions"):
        await resolver.resolve_workflow(plan.source, services.catalog)


@pytest.mark.asyncio
async def test_external_catalog_can_require_integrity_pins(services):
    resolver = _resolver(
        lambda _request: httpx.Response(200, text=FUNCTION_YAML),
    )
    policy = resolver.policies["trusted"].model_copy(update={"require_integrity_pin": True})
    resolver.policies["trusted"] = policy
    plan = compile_workflow(_workflow(), trusted_catalogs={"trusted": policy})
    with pytest.raises(ToolError, match="integrity pin"):
        await resolver.resolve_workflow(plan.source, services.catalog)


def test_external_catalog_transport_controls_are_strict():
    with pytest.raises(ValueError, match="redirects"):
        ExternalCatalogConfig(allowed_hosts=["catalog.test"], follow_redirects=True)
    with pytest.raises(ValueError, match="TLS"):
        ExternalCatalogConfig(allowed_hosts=["catalog.test"], verify_tls=False)
