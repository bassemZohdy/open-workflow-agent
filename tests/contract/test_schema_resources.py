from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from engine_cases import engine_cases
from open_workflow_agent.config import ExternalCatalogConfig
from open_workflow_agent.errors import (
    ToolError,
    UnsupportedWorkflowFeature,
    WorkflowSchemaError,
    WorkflowSemanticError,
)
from open_workflow_agent.external_catalog import ExternalCatalogResolver
from open_workflow_agent.protocols import HttpClient
from open_workflow_agent.sandbox import resolve_and_compile_sandbox_workflow
from open_workflow_agent.workflow import generate_default_workflow, resolve_and_compile_workflow

ROOT = "https://schemas.test/root.json"
CHILD = "https://schemas.test/number.json"


def _resolver(services, documents, *, max_bytes=4_000_000, cache_ttl=0, handler=None):
    raw = {
        uri: (value.encode() if isinstance(value, str) else json.dumps(value).encode())
        for uri, value in documents.items()
    }
    fetched = []

    async def respond(request):
        fetched.append(str(request.url))
        if handler is not None:
            return await handler(request)
        return httpx.Response(200, content=raw[str(request.url)])

    policy = ExternalCatalogConfig(
        allowed_hosts=["schemas.test"],
        allowed_endpoints=list(raw),
        integrity_pins={uri: hashlib.sha256(body).hexdigest() for uri, body in raw.items()},
        max_response_bytes=max_bytes,
        cache_ttl_seconds=cache_ttl,
    )
    resolver = ExternalCatalogResolver(
        {},
        resource_policies={"schemas": policy},
        http=HttpClient(transport=httpx.MockTransport(respond)),
    )
    services.external_catalogs = resolver
    return resolver, fetched, raw


def _workflow(schema):
    workflow = generate_default_workflow()
    workflow["input"] = {"schema": schema}
    workflow["do"] = [{"finish": {"set": "${ . }"}}]
    return workflow


async def _compile(services, resolver, source, sandbox=False):
    options = dict(resolver=resolver, catalog=services.catalog, trusted_catalogs=resolver.policies)
    if sandbox:
        return await resolve_and_compile_sandbox_workflow(
            source, sandbox=services.config.sandbox, **options
        )
    return await resolve_and_compile_workflow(source, **options)


async def _invoke(services, engine_name, engine_type, plan, data):
    engine = engine_type()
    await engine.initialize(services)
    handle = services.invocations.create(
        engine=engine_name,
        session_id=None,
        user_id="user",
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    try:
        return await engine.invoke(await engine.compile(plan), handle, data)
    finally:
        await engine.shutdown()


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("sandbox", [False, True])
@pytest.mark.asyncio
async def test_schema_resource_closure_is_frozen_offline_and_validates_before_effects(
    services, engine_name, engine_type, sandbox
):
    documents = {
        ROOT: {
            "type": "object",
            "properties": {"value": {"$ref": "number.json#integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        CHILD: {"$anchor": "integer", "type": "integer", "minimum": 1},
    }
    resolver, fetched, _ = _resolver(services, documents)
    workflow = _workflow({"resource": {"endpoint": ROOT}})
    workflow["do"] = [
        {
            "finish": {
                "set": {
                    "value": "${ .value }",
                    "source": "${ $workflow.definition }",
                    "definition": "${ $task.definition }",
                }
            }
        }
    ]
    plan = await _compile(services, resolver, workflow, sandbox)
    assert plan.source == workflow
    assert {item.uri for item in plan.resources} == {ROOT, CHILD}
    assert plan.execution_source["input"]["schema"] == {"document": {"$ref": ROOT}}
    next(item for item in plan.resources if item.uri == CHILD).document["minimum"] = 99
    valid = await _invoke(services, engine_name, engine_type, plan, {"value": 2})
    assert valid.status == "completed", valid.error
    assert valid.output == {
        "value": 2,
        "source": workflow,
        "definition": workflow["do"][0]["finish"],
    }
    invalid = await _invoke(services, engine_name, engine_type, plan, {"value": 0})
    assert invalid.status == "faulted", invalid.error
    assert invalid.error["details"]["validator"] == "minimum"
    assert len(fetched) == 2


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_resource_schemas_cover_reusable_input_output_and_export(
    services, engine_name, engine_type
):
    document = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }
    resolver, fetched, _ = _resolver(services, {ROOT: document})
    schema = {"resource": {"endpoint": ROOT}}
    function = {
        "input": {"schema": schema},
        "set": {"value": "${ .value + 1 }"},
        "output": {"schema": schema},
        "export": {"schema": schema, "as": "${ . }"},
    }
    source = _workflow(schema)
    source["use"] = {"functions": {"increment": function}}
    source["do"] = [
        {"call": {"call": "increment", "output": {"schema": schema, "as": "${ $context }"}}}
    ]
    source["output"] = {"schema": schema}
    plan = await _compile(services, resolver, source)
    result = await _invoke(services, engine_name, engine_type, plan, {"value": 1})
    assert result.status == "completed", result.error
    assert result.output == {"value": 2}
    assert fetched == [ROOT]


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_imported_schema_pipeline_preserves_descriptors_and_opaque_protocol_data(
    services, engine_name, engine_type
):
    schemas = {
        "$defs": {
            "input": {
                "type": "object",
                "properties": {"value": {"type": "integer", "minimum": 1}},
                "required": ["value"],
            },
            "output": {"type": "object", "required": ["value", "definition", "opaque"]},
            "context": {"const": {"done": True}},
        }
    }
    fetched, effects = [], []

    def schema(name):
        return {"resource": {"endpoint": f"{ROOT}#/$defs/{name}"}}

    definition = {
        "input": {"schema": schema("input")},
        "call": "http",
        "with": {
            "method": "post",
            "endpoint": "https://api.test/echo",
            "body": {
                "value": "${ .value }",
                "definition": "${ $task.definition }",
                "opaque": "${ .opaque }",
            },
        },
        "output": {"schema": schema("output")},
        "export": {"as": "${ {done: true} }", "schema": schema("context")},
    }

    async def respond(request):
        if request.url.host == "catalog.test":
            fetched.append(str(request.url))
            return httpx.Response(200, json=definition)
        if request.url.host == "schemas.test":
            fetched.append(str(request.url))
            return httpx.Response(200, content=json.dumps(schemas).encode())
        effects.append(json.loads(request.content))
        return httpx.Response(200, json=effects[-1])

    resolver = ExternalCatalogResolver(
        {"trusted": ExternalCatalogConfig(allowed_hosts=["catalog.test", "api.test"])},
        resource_policies={
            "schemas": ExternalCatalogConfig(
                allowed_hosts=["schemas.test"],
                allowed_endpoints=[ROOT],
                integrity_pins={ROOT: hashlib.sha256(json.dumps(schemas).encode()).hexdigest()},
            )
        },
        http=HttpClient(transport=httpx.MockTransport(respond)),
    )
    services.external_catalogs = resolver
    source = generate_default_workflow()
    source["use"] = {"catalogs": {"trusted": {"endpoint": "https://catalog.test/root"}}}
    source["do"] = [
        {
            "remote": {
                "call": "echo:1.0.0@trusted",
                "output": {"as": "${ {result: ., context: $context} }"},
            }
        }
    ]
    plan = await _compile(services, resolver, source)
    assert plan.source == source
    assert plan.imports[0].definition == definition
    assert plan.imports[0].execution_definition["input"]["schema"] == {
        "document": {"$ref": f"{ROOT}#/$defs/input"}
    }
    opaque = {"endpoint": "http://127.0.0.1/private", "cookie": "data", "url": "{missing}"}
    result = await _invoke(services, engine_name, engine_type, plan, {"value": 2, "opaque": opaque})
    expected = {"value": 2, "definition": definition, "opaque": opaque}
    assert result.status == "completed", result.error
    assert result.output == {"result": expected, "context": {"done": True}}
    invalid = await _invoke(
        services, engine_name, engine_type, plan, {"value": 0, "opaque": opaque}
    )
    assert invalid.status == "faulted", invalid.error
    assert invalid.error["details"]["validator"] == "minimum"
    assert effects == [expected]
    assert len(fetched) == 2


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.parametrize("document,valid", [(True, True), (False, False)])
@pytest.mark.asyncio
async def test_boolean_resource_schemas(services, engine_name, engine_type, document, valid):
    resolver, fetched, _ = _resolver(services, {ROOT: document})
    plan = await _compile(services, resolver, _workflow({"resource": {"endpoint": ROOT}}))
    result = await _invoke(services, engine_name, engine_type, plan, None)
    assert result.status == ("completed" if valid else "faulted"), result.error
    assert fetched == [ROOT]


@pytest.mark.parametrize("reference", [ROOT, ROOT + "#integer"])
@pytest.mark.asyncio
async def test_inline_external_references_use_the_same_pinned_closure(services, reference):
    resolver, fetched, _ = _resolver(services, {ROOT: {"$anchor": "integer", "type": "integer"}})
    plan = await _compile(services, resolver, _workflow({"document": {"$ref": reference}}))
    assert len(plan.resources) == 1
    assert fetched == [ROOT]


@pytest.mark.asyncio
async def test_conflicting_captured_schema_identifiers_fail_admission(services):
    resolver, _, _ = _resolver(
        services,
        {
            ROOT: {"$id": "https://schemas.test/shared", "type": "integer"},
            CHILD: {"$id": "https://schemas.test/shared", "type": "string"},
        },
    )
    source = _workflow({"resource": {"endpoint": ROOT}})
    source["output"] = {"schema": {"resource": {"endpoint": CHILD}}}
    with pytest.raises(WorkflowSemanticError, match="conflicting schema resource identifiers"):
        await _compile(services, resolver, source)


@pytest.mark.asyncio
async def test_same_schema_resource_can_be_captured_under_multiple_urls(services):
    document = {"$id": "https://schemas.test/shared", "type": "integer"}
    resolver, fetched, _ = _resolver(services, {ROOT: document, CHILD: document})
    source = _workflow({"resource": {"endpoint": ROOT}})
    source["output"] = {"schema": {"resource": {"endpoint": CHILD}}}
    plan = await _compile(services, resolver, source)
    assert len(plan.resources) == 2
    assert len(fetched) == 2


@pytest.mark.asyncio
async def test_literal_catalog_resource_and_ref_fields_are_never_fetch_slots(services):
    resolver, fetched, _ = _resolver(services, {ROOT: True})
    data = {"resource": {"endpoint": "https://unapproved.test"}, "$ref": "https://unapproved.test"}
    source = _workflow({"document": {"const": data}})
    source["do"] = [{"finish": {"set": data}}]
    plan = await _compile(services, resolver, source)
    assert plan.resources == ()
    assert fetched == []


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_sandbox_admission_preserves_literal_run_fields_in_schemas_and_reusable_data(
    services, engine_name, engine_type
):
    resolver, fetched, _ = _resolver(services, {ROOT: True})
    data = {"run": {"container": {"image": "ordinary-data"}, "await": False}}
    source = _workflow({"document": {"const": data}})
    source["use"] = {"functions": {"data": {"set": data}}}
    source["do"] = [{"finish": {"call": "data"}}]
    plan = await _compile(services, resolver, source, sandbox=True)
    result = await _invoke(services, engine_name, engine_type, plan, data)
    assert result.status == "completed", result.error
    assert result.output == data
    assert fetched == []


@pytest.mark.parametrize(
    "case",
    [
        "unapproved",
        "missing-pin",
        "wrong-pin",
        "oversize",
        "malformed",
        "missing-fragment",
        "private",
        "authentication",
        "dynamic",
        "unsupported-task",
    ],
)
@pytest.mark.asyncio
async def test_schema_resource_admission_fails_closed(services, case):
    uri = "https://127.0.0.1/root" if case == "private" else ROOT
    document = "type: integer\ntype: string\n" if case == "malformed" else {"type": "integer"}
    resolver, fetched, _ = _resolver(
        services, {uri: document}, max_bytes=1 if case == "oversize" else 4_000_000
    )
    policy = resolver.schemas.policies["schemas"]
    endpoint = uri
    if case == "private":
        policy.allowed_hosts = ["127.0.0.1"]
    elif case == "unapproved":
        policy.allowed_endpoints = []
    elif case == "missing-pin":
        policy.integrity_pins = {}
    elif case == "wrong-pin":
        policy.integrity_pins = {uri: "0" * 64}
    elif case == "missing-fragment":
        endpoint += "#missing"
    elif case == "dynamic":
        endpoint = "${ .endpoint }"
    if case == "authentication":
        endpoint = {"uri": uri, "authentication": {"bearer": {"token": "secret"}}}
    source = _workflow({"resource": {"endpoint": endpoint}})
    if case == "unsupported-task":
        # Event consumption is now implemented; deployment-disabled execution
        # still has to fail before any external schema is fetched.
        source["do"] = [
            {"bad": {"run": {"script": {"language": "python", "code": "print('unused')"}}}}
        ]
    with pytest.raises(
        (ToolError, UnsupportedWorkflowFeature, WorkflowSchemaError, WorkflowSemanticError)
    ):
        await _compile(services, resolver, source)
    if case in {
        "unapproved",
        "missing-pin",
        "private",
        "authentication",
        "dynamic",
        "unsupported-task",
    }:
        assert not fetched
    assert not services.model.calls


@pytest.mark.asyncio
async def test_resources_are_disabled_without_deployment_policy(services):
    resolver = services.external_catalogs
    with pytest.raises(UnsupportedWorkflowFeature, match="external data schema"):
        await _compile(services, resolver, _workflow({"resource": {"endpoint": ROOT}}))


@pytest.mark.asyncio
async def test_resource_policy_is_rechecked_without_refetch(services):
    resolver, fetched, _ = _resolver(services, {ROOT: True})
    plan = await _compile(services, resolver, _workflow({"resource": {"endpoint": ROOT}}))
    resolver.schemas.policies["schemas"].integrity_pins[ROOT] = "0" * 64
    with pytest.raises(ToolError, match="policy has changed"):
        resolver.schemas.verify(plan.resources)
    assert fetched == [ROOT]


@pytest.mark.asyncio
async def test_resource_cache_revalidation_preserves_pin_and_original_bytes(services):
    raw = b'{"type": "integer"}\n'
    calls = []

    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, content=raw, headers={"etag": '"v1"'})
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304)

    resolver, _, _ = _resolver(services, {ROOT: raw.decode()}, handler=handler)
    first = await _compile(services, resolver, _workflow({"resource": {"endpoint": ROOT}}))
    second = await _compile(services, resolver, _workflow({"resource": {"endpoint": ROOT}}))
    assert first.resources == second.resources
    assert first.fingerprint == second.fingerprint
    assert len(calls) == 2


@pytest.mark.parametrize(
    "case", ["nodes", "bytes", "surrogate-value", "surrogate-key", "huge-integer"]
)
def test_serialization_rejects_unbounded_alias_expansion_and_invalid_utf8(case):
    from open_workflow_agent.workflow import load_workflow

    if case == "nodes":
        value = {"items": [None] * 100_001}
    elif case == "bytes":
        shared = {"description": "x" * 1000}
        value = {"items": [shared] * 20_000}
    elif case == "surrogate-value":
        value = {"value": chr(0xD800)}
    elif case == "surrogate-key":
        value = {chr(0xD800): "value"}
    else:
        value = {"value": 1 << 15000}
    with pytest.raises(WorkflowSchemaError):
        load_workflow(value)


@pytest.mark.asyncio
async def test_dependency_cycles_are_a_finite_pinned_closure(services):
    documents = {
        ROOT: {"$ref": "number.json"},
        CHILD: {"type": "object", "properties": {"child": {"$ref": "root.json"}}},
    }
    resolver, fetched, _ = _resolver(services, documents)
    plan = await _compile(services, resolver, _workflow({"resource": {"endpoint": ROOT}}))
    assert len(plan.resources) == len(fetched) == 2


@pytest.mark.parametrize("case", ["documents", "bytes"])
@pytest.mark.asyncio
async def test_dependency_closure_limits_are_enforced_before_further_requests(
    services, monkeypatch, case
):
    import open_workflow_agent.resources as resource_module

    documents = {ROOT: {"$ref": "number.json"}, CHILD: {"type": "integer"}}
    resolver, fetched, _ = _resolver(services, documents)
    if case == "documents":
        monkeypatch.setattr(resource_module, "MAX_SCHEMA_RESOURCES", 1)
    else:
        monkeypatch.setattr(resource_module, "MAX_SCHEMA_CLOSURE_BYTES", 1)
    with pytest.raises(WorkflowSemanticError, match="closure exceeds"):
        await _compile(services, resolver, _workflow({"resource": {"endpoint": ROOT}}))
    assert fetched == [ROOT]


@pytest.mark.asyncio
async def test_ambiguous_resource_policy_does_not_fetch(services):
    resolver, fetched, _ = _resolver(services, {ROOT: True})
    resolver.schemas.policies["other"] = resolver.schemas.policies["schemas"].model_copy(deep=True)
    with pytest.raises(UnsupportedWorkflowFeature, match="one exact"):
        await _compile(services, resolver, _workflow({"resource": {"endpoint": ROOT}}))
    assert fetched == []


@pytest.mark.asyncio
async def test_resource_digest_changes_identity_and_existing_plan_uses_its_captured_schema(
    services,
):
    body = {"value": True}

    async def handler(request):
        return httpx.Response(200, content=json.dumps(body["value"]).encode())

    resolver, fetched, _ = _resolver(services, {ROOT: True}, handler=handler)
    source = _workflow({"resource": {"endpoint": ROOT}})
    first = await _compile(services, resolver, source)
    first_pin = first.resources[0].digest
    body["value"] = False
    resolver.schemas.policies["schemas"].integrity_pins[ROOT] = hashlib.sha256(b"false").hexdigest()
    second = await _compile(services, resolver, source)
    assert first.source == second.source
    assert first.fingerprint != second.fingerprint
    resolver.schemas.policies["schemas"].integrity_pins[ROOT] = first_pin
    from open_workflow_agent.workflow import WorkflowExecutor

    assert await WorkflowExecutor(services.catalog, services=services).execute(first, None) is None
    assert len(fetched) == 2


@pytest.mark.parametrize("engine_name,engine_type", engine_cases())
@pytest.mark.asyncio
async def test_revoked_resource_trust_stops_before_model_or_network_effects(
    services, engine_name, engine_type
):
    resolver, fetched, _ = _resolver(services, {ROOT: True})
    source = _workflow({"resource": {"endpoint": ROOT}})
    source["do"] = [{"model": {"call": "llm:1.0.0@default"}}]
    plan = await _compile(services, resolver, source)
    resolver.schemas.policies["schemas"].allowed_endpoints = []
    result = await _invoke(services, engine_name, engine_type, plan, None)
    assert result.status == "faulted"
    assert not services.model.calls
    assert fetched == [ROOT]


@pytest.mark.parametrize("prebound", [False, True])
@pytest.mark.asyncio
async def test_resource_credentials_remain_deployment_owned(services, monkeypatch, prebound):
    from open_workflow_agent.config import CatalogAuthenticationConfig
    from open_workflow_agent.security import SecurityConfig

    monkeypatch.setenv("OWA_SCHEMA_TOKEN", "schema-secret")
    received = []

    async def handler(request):
        received.append(request.headers.get("authorization"))
        return httpx.Response(200, content=b"true")

    resolver, _, _ = _resolver(services, {ROOT: True}, handler=handler)
    resolver.security = SecurityConfig.model_validate(
        {
            "profiles": {
                "schema-reader": {"type": "bearer", "token": {"from_env": "OWA_SCHEMA_TOKEN"}}
            }
        }
    )
    resolver.schemas.policies["schemas"].authentication = CatalogAuthenticationConfig(
        security_profile="schema-reader"
    )
    if prebound:
        from open_workflow_agent.security import ProfileAuthentication

        resolver.http.authentication = ProfileAuthentication(resolver.security, "schema-reader")
        resolver.security = None
    plan = await _compile(services, resolver, _workflow({"resource": {"endpoint": ROOT}}))
    assert received == ["Bearer schema-secret"]
    assert "schema-secret" not in plan.source_json
    assert all("schema-secret" not in item.document_json for item in plan.resources)


@pytest.mark.parametrize(
    "body",
    [
        "a: &a {child: *a}",
        "[1, 2]",
        "type: integer\nminimum: .nan",
        "type: integer\nminimum: 1.0\n$schema: http://json-schema.org/draft-07/schema#",
    ],
)
@pytest.mark.asyncio
async def test_untrusted_schema_documents_fail_safe_loading_or_dialect_validation(services, body):
    resolver, _, _ = _resolver(services, {ROOT: body})
    with pytest.raises((WorkflowSchemaError, WorkflowSemanticError, UnsupportedWorkflowFeature)):
        await _compile(services, resolver, _workflow({"resource": {"endpoint": ROOT}}))


def test_yaml_alias_expansion_is_bounded_before_construction():
    from open_workflow_agent.workflow import load_json_value

    text = "seed: &seed [null, null]\n"
    for index in range(20):
        previous = "seed" if index == 0 else f"level{index - 1}"
        text += f"level{index}: &level{index} [*{previous}, *{previous}]\n"
    with pytest.raises(WorkflowSchemaError, match="YAML expansion"):
        load_json_value(text)


def test_resource_configuration_keeps_exact_urls_and_mandatory_pins():
    from open_workflow_agent.config import ExternalResourceConfig
    from pydantic import ValidationError

    uri = ROOT + "/"
    policy = ExternalResourceConfig(allowed_endpoints=[uri])
    assert policy.allowed_endpoints == [uri]
    assert policy.require_integrity_pin is True
    with pytest.raises(ValidationError):
        ExternalResourceConfig(require_integrity_pin=False)
