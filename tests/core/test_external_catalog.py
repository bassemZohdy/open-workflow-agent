from __future__ import annotations

import hashlib
import json
import socket

import httpx
import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import ExternalCatalogConfig, RuntimeConfig
from open_workflow_agent.errors import ToolError, UnsupportedWorkflowFeature, WorkflowSchemaError
from open_workflow_agent.external_catalog import (
    CatalogFunctionReference,
    ExternalCatalogResolver,
    _authentication,
    _contains_key,
    _contains_sensitive_header,
    _endpoint_uri,
    _function_uri,
    _header,
    _HostBoundAuthentication,
    _nested_values,
    _referenced_functions,
    _resolve_external_destination,
    _validate_function_definition,
    _validate_invocation_endpoints,
    _validate_network_destination,
    parse_catalog_function_reference,
)
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.security import SecurityConfig
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import (
    WorkflowExecutor,
    compile_workflow,
    resolve_and_compile_workflow,
)


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


@pytest.mark.asyncio
async def test_catalog_resolution_precedes_plan_construction(services):
    order: list[str] = []

    class RecordingResolver:
        async def resolve_workflow(self, workflow, catalog):
            del workflow, catalog
            order.append("resolve")
            return {}

    policy = ExternalCatalogConfig(allowed_hosts=["catalog.test"])
    plan = await resolve_and_compile_workflow(
        _workflow(),
        trusted_catalogs={"trusted": policy},
        resolver=RecordingResolver(),
        catalog=services.catalog,
    )
    order.append("plan")

    assert order == ["resolve", "plan"]
    assert plan.name == "external"


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
@pytest.mark.parametrize(
    ("endpoint", "error_type", "message"),
    [
        ("http://catalog.test/root", UnsupportedWorkflowFeature, "HTTPS"),
        ("https://other.test/root", UnsupportedWorkflowFeature, "host is not allowed"),
        ("https://user:password@catalog.test/root", WorkflowSchemaError, "credentials"),
    ],
)
async def test_external_catalog_rejects_untrusted_endpoint_variants(
    services, endpoint, error_type, message
):
    workflow = _workflow()
    workflow["use"]["catalogs"]["trusted"]["endpoint"]["uri"] = endpoint  # type: ignore[index]
    policy = ExternalCatalogConfig(allowed_hosts=["catalog.test"])
    resolver = ExternalCatalogResolver(
        {"trusted": policy},
        http=HttpClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
    )
    plan = compile_workflow(workflow, trusted_catalogs={"trusted": policy})

    with pytest.raises(error_type, match=message):
        await resolver.resolve_workflow(plan.source, services.catalog)


@pytest.mark.asyncio
async def test_external_catalog_rejects_malformed_endpoint(services):
    workflow = _workflow()
    workflow["use"]["catalogs"]["trusted"]["endpoint"]["uri"] = "https://[invalid/root"  # type: ignore[index]
    policy = ExternalCatalogConfig(allowed_hosts=["catalog.test"])
    resolver = ExternalCatalogResolver({"trusted": policy}, http=HttpClient())
    plan = compile_workflow(workflow, trusted_catalogs={"trusted": policy})

    with pytest.raises(WorkflowSchemaError, match="endpoint is malformed"):
        await resolver.resolve_workflow(plan.source, services.catalog)


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
async def test_external_catalog_rechecks_pin_on_not_modified_revalidation(services):
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, text=FUNCTION_YAML, headers={"ETag": '"function-v1"'})
        return httpx.Response(304, headers={"ETag": '"function-v1"'})

    digest = hashlib.sha256(FUNCTION_YAML.encode()).hexdigest()
    resolver = _resolver(handler, pins={"echo:1.0.0@trusted": digest})
    plan = compile_workflow(_workflow(), trusted_catalogs={"trusted": resolver.policies["trusted"]})
    await resolver.resolve_workflow(plan.source, services.catalog)
    resolver.policies["trusted"] = resolver.policies["trusted"].model_copy(
        update={"integrity_pins": {"echo:1.0.0@trusted": "0" * 64}}
    )

    with pytest.raises(ToolError, match="integrity verification failed"):
        await resolver.resolve_workflow(plan.source, services.catalog)


@pytest.mark.asyncio
async def test_external_catalog_rejects_changed_pinned_definition(services):
    changed_definition = FUNCTION_YAML + "\n# changed\n"
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            text=FUNCTION_YAML if calls == 1 else changed_definition,
            headers={"ETag": f'"function-v{calls}"'},
        )

    digest = hashlib.sha256(FUNCTION_YAML.encode()).hexdigest()
    resolver = _resolver(handler, pins={"echo:1.0.0@trusted": digest})
    plan = compile_workflow(_workflow(), trusted_catalogs={"trusted": resolver.policies["trusted"]})
    await resolver.resolve_workflow(plan.source, services.catalog)

    with pytest.raises(ToolError, match="integrity verification failed"):
        await resolver.resolve_workflow(plan.source, services.catalog)
    assert resolver._cache[next(iter(resolver._cache))].digest == digest


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
async def test_external_catalog_rejects_malformed_payload_and_sanitizes_timeout(services):
    async def malformed_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="[")

    malformed_resolver = _resolver(malformed_handler)
    malformed_plan = compile_workflow(
        _workflow(), trusted_catalogs={"trusted": malformed_resolver.policies["trusted"]}
    )
    with pytest.raises(WorkflowSchemaError, match="not valid YAML") as malformed:
        await malformed_resolver.resolve_workflow(malformed_plan.source, services.catalog)
    assert "catalog.test" not in str(malformed.value)

    async def timeout_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private timeout details")

    timeout_resolver = _resolver(timeout_handler)
    timeout_plan = compile_workflow(
        _workflow(), trusted_catalogs={"trusted": timeout_resolver.policies["trusted"]}
    )
    with pytest.raises(ToolError, match="external catalog resolution failed") as timeout:
        await timeout_resolver.resolve_workflow(timeout_plan.source, services.catalog)
    assert "private timeout details" not in str(timeout.value)
    assert "catalog.test" not in repr(timeout.value.details)


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
async def test_external_catalog_rejects_authorization_headers_in_function_definition(services):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""\
call: http
with:
  endpoint: https://api.test/echo
  headers:
    Authorization: Bearer secret
""",
        )

    resolver = _resolver(handler)
    plan = compile_workflow(_workflow(), trusted_catalogs={"trusted": resolver.policies["trusted"]})
    with pytest.raises(UnsupportedWorkflowFeature, match="authorization headers") as error:
        await resolver.resolve_workflow(plan.source, services.catalog)
    assert "secret" not in str(error.value)


def test_external_catalog_requires_exact_semantic_version():
    workflow = _workflow()
    workflow["do"][0]["remote"]["call"] = "echo:latest@trusted"  # type: ignore[index]
    policy = ExternalCatalogConfig(allowed_hosts=["catalog.test"])

    with pytest.raises(UnsupportedWorkflowFeature, match="is not enabled"):
        compile_workflow(workflow, trusted_catalogs={"trusted": policy})


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


def test_external_catalog_reference_and_uri_helpers():
    reference = parse_catalog_function_reference("echo:1.2.3@trusted_catalog")
    assert reference == CatalogFunctionReference("echo", "1.2.3", "trusted_catalog")
    assert reference is not None and reference.value == "echo:1.2.3@trusted_catalog"
    assert parse_catalog_function_reference("echo:latest@trusted") is None
    assert parse_catalog_function_reference("Echo:1.2.3@trusted") is None

    assert _function_uri("https://catalog.test/root", reference) == (
        "https://catalog.test/root/functions/echo/1.2.3/function.yaml"
    )
    assert _function_uri("https://github.com/acme/catalog", reference).endswith(
        "/acme/catalog/refs/heads/main/functions/echo/1.2.3/function.yaml"
    )
    assert _function_uri("https://github.com/acme/catalog/tree/release/v1", reference).endswith(
        "/acme/catalog/refs/heads/release/v1/functions/echo/1.2.3/function.yaml"
    )
    assert _function_uri("https://gitlab.com/acme/catalog", reference).endswith(
        "/acme/catalog/-/raw/main/functions/echo/1.2.3/function.yaml"
    )
    assert _function_uri("https://gitlab.com/acme/catalog/-/tree/release", reference).endswith(
        "/acme/catalog/-/raw/release/functions/echo/1.2.3/function.yaml"
    )
    with pytest.raises(WorkflowSchemaError, match="owner and repository"):
        _function_uri("https://github.com/acme", reference)

    assert _endpoint_uri("https://catalog.test") == "https://catalog.test"
    assert _endpoint_uri({"endpoint": "https://catalog.test"}) == "https://catalog.test"
    assert _endpoint_uri({"endpoint": {"uri": "https://catalog.test"}}) == ("https://catalog.test")
    assert _endpoint_uri({"endpoint": 1}) is None
    with pytest.raises(WorkflowSchemaError, match="authentication"):
        _endpoint_uri({"endpoint": {"authentication": {}}})


def test_external_catalog_definition_and_recursive_helpers():
    reference = CatalogFunctionReference("echo", "1.0.0", "trusted")
    valid = {"call": "http", "with": {"endpoint": "https://api.test"}}
    assert _validate_function_definition(valid, reference) == ("http", valid["with"])
    assert _contains_key({"nested": [{"$ref": "x"}]}, "$ref") is True
    assert _contains_key({"nested": []}, "$ref") is False
    assert _nested_values({"a": [1, {"b": 2}]}) == [
        {"a": [1, {"b": 2}]},
        [1, {"b": 2}],
        1,
        {"b": 2},
        2,
    ]
    assert _contains_sensitive_header({"nested": [{"Cookie": "secret"}]}) is True
    assert _contains_sensitive_header({"safe": "value"}) is False

    for definition, message in [
        ({"with": {"$ref": "x"}}, "references are disabled"),
        ({"run": {"script": {}}}, "script functions"),
        ({"call": "stdio"}, "supported protocol"),
        ({"call": "http", "with": []}, "invalid with"),
        ({"call": "http", "with": {"headers": {"authorization": "secret"}}}, "authorization"),
    ]:
        with pytest.raises((UnsupportedWorkflowFeature, WorkflowSchemaError), match=message):
            _validate_function_definition(definition, reference)

    workflow = {"do": [{"call": "echo:1.0.0@trusted"}, {"call": "noop:1.0.0@default"}]}
    assert _referenced_functions(workflow) == {reference}


def test_external_catalog_invocation_destinations_and_headers(monkeypatch):
    policy = ExternalCatalogConfig(allowed_hosts=["api.test"])
    _validate_invocation_endpoints(
        {"body": [{"endpoint": "https://api.test/one"}, {"uri": "https://api.test/two"}]},
        policy,
    )
    with pytest.raises(UnsupportedWorkflowFeature, match="host is not allowed"):
        _validate_invocation_endpoints({"url": "https://other.test"}, policy)
    assert _header({"ETag": "v1"}, "etag") == "v1"
    assert _header({}, "etag") is None

    class Provider:
        def headers(self, endpoint: str) -> dict[str, str]:
            return {"X-Endpoint": endpoint}

    bound = _HostBoundAuthentication(Provider(), "api.test")
    assert bound.headers("https://api.test/one") == {"X-Endpoint": "https://api.test/one"}
    assert bound.headers("https://other.test/one") == {}

    assert _authentication(type("Auth", (), {"security_profile": None})(), None) is None
    with pytest.raises(ToolError, match="runtime security configuration"):
        _authentication(type("Auth", (), {"security_profile": "missing"})(), None)
    monkeypatch.setenv("OWA_CATALOG_TEST_TOKEN", "secret")
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "catalog": {
                    "type": "bearer",
                    "token": {"from_env": "OWA_CATALOG_TEST_TOKEN"},
                }
            }
        }
    )
    assert _authentication(type("Auth", (), {"security_profile": "catalog"})(), security).headers(
        "https://api.test"
    ) == {"Authorization": "Bearer secret"}


@pytest.mark.asyncio
async def test_external_catalog_destination_resolution_failures(monkeypatch):
    policy = ExternalCatalogConfig(allowed_hosts=["api.test"])
    import open_workflow_agent.external_catalog as module

    async def approved(_uri: str, *, timeout: float) -> tuple[str, ...]:
        assert timeout == policy.timeout_seconds
        return ("93.184.216.34",)

    monkeypatch.setattr(module, "resolve_public_addresses_async", approved)
    assert await _resolve_external_destination("https://api.test", policy) == ("93.184.216.34",)

    async def denied(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        raise ToolError("disallowed IP address")

    monkeypatch.setattr(module, "resolve_public_addresses_async", denied)
    with pytest.raises(UnsupportedWorkflowFeature, match="disallowed IP"):
        await _resolve_external_destination("https://api.test", policy)

    async def failed(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        raise ToolError("resolver unavailable")

    monkeypatch.setattr(module, "resolve_public_addresses_async", failed)
    with pytest.raises(ToolError, match="could not be resolved"):
        await _resolve_external_destination("https://api.test", policy)

    with pytest.raises(WorkflowSchemaError, match="malformed"):
        await _resolve_external_destination("https://[invalid", policy)


@pytest.mark.asyncio
async def test_external_catalog_network_destination_validation(monkeypatch):
    policy = ExternalCatalogConfig(allowed_hosts=["api.test"])
    mock_client = HttpClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    await _validate_network_destination("https://api.test", policy, mock_client)
    await _validate_network_destination("https://", policy, mock_client)
    with pytest.raises(UnsupportedWorkflowFeature, match="disallowed IP"):
        await _validate_network_destination("https://127.0.0.1", policy, mock_client)

    real_client = HttpClient()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    await _validate_network_destination("https://api.test", policy, real_client)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(ToolError, match="could not be resolved"):
        await _validate_network_destination("https://api.test", policy, real_client)
