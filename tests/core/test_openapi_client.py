"""Finite operation routing/codec/security witnesses; no paid services."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import ssl
from dataclasses import replace

import httpx
import open_workflow_agent.security as security_module
import pytest
import yaml
from _auth_tls_fixture import auth_tls_receiver
from open_workflow_agent.errors import ToolError
from open_workflow_agent.openapi_client import (
    MAX_DOCUMENT_BYTES,
    OpenApiDescription,
    OpenApiOperationSelection,
    _OpenApiOutcomeUncertain,
    call_openapi,
    fetch_validate_description,
    prepare_request,
    select_operation,
)
from open_workflow_agent.protocols import HttpClient, ProtocolServices
from open_workflow_agent.security import ProfileAuthentication, SecurityConfig

DOCUMENT = "https://description.test/spec.json"
TARGET = "https://api.test/v1/items/42"


def source(version="3.0.3"):
    return {
        "openapi": version,
        "info": {"title": "Finite", "version": "1.0.0"},
        "servers": [{"url": "https://api.test/v1"}],
        "paths": {
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "Item"}},
                }
            }
        },
    }


def operation(document):
    return document["paths"]["/items/{id}"]["get"]


def selection(document=None, profile=None):
    return select_operation(
        OpenApiDescription.capture(DOCUMENT, document or source()),
        "getItem",
        credential_profile=profile,
    )


def payload(**overrides):
    return {"operationId": "getItem", "parameters": {"id": "42"}, **overrides}


@pytest.mark.asyncio
async def test_description_operation_selector_is_not_a_business_action_identity():
    keys = []

    async def peer(request):
        keys.append(request.headers["Idempotency-Key"])
        return httpx.Response(200, json={"id": "42"})

    services = ProtocolServices(HttpClient(transport=httpx.MockTransport(peer)))
    selected = selection()
    for _ in range(2):
        assert await services.call("openapi", payload(), _openapi_selection=selected) == {
            "id": "42"
        }
    for _ in range(2):
        await services.call(
            "openapi", payload(operation_id="retained-action"), _openapi_selection=selected
        )
    assert len(keys) == 4 and len(set(keys[:2])) == 2
    assert all(key != "getItem" for key in keys)
    assert keys[2:] == ["retained-action", "retained-action"]


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["json", "yaml"])
async def test_public_description_fetch_strips_global_credentials_and_certificate(encoding):
    calls = []

    class ForbiddenAuthentication:
        def headers(self, endpoint):
            pytest.fail("public descriptions cannot resolve business credentials")

    async def receiver(request):
        calls.append(request)
        assert request.method == "GET" and "authorization" not in request.headers
        raw = (
            json.dumps(source()).encode()
            if encoding == "json"
            else yaml.safe_dump(source()).encode()
        )
        return httpx.Response(200, content=raw, headers={"Content-Type": "application/" + encoding})

    client = HttpClient(
        transport=httpx.MockTransport(receiver),
        authentication=ForbiddenAuthentication(),
        client_cert=("never-open-cert", "never-open-key"),
    )
    description = await fetch_validate_description(client, {"endpoint": DOCUMENT}, timeout=1)
    assert description.document() == source() and len(calls) == 1
    assert "never-open" not in description.document_json


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "duplicate-json",
        "duplicate-yaml",
        "alias",
        "tag",
        "oversize",
        "nan",
        "version",
        "content-type",
        "redirect",
    ],
)
async def test_description_failures_are_bounded_sanitized_and_never_dispatch_business(damage):
    raw, content_type, status = json.dumps(source()).encode(), "application/json", 200
    if damage == "duplicate-json":
        raw = b'{"openapi":"3.0.3","openapi":"3.2.0"}'
    elif damage == "duplicate-yaml":
        raw, content_type = b"openapi: '3.0.3'\nopenapi: '3.2.0'", "application/yaml"
    elif damage == "alias":
        raw, content_type = b"x: &secret secret-marker\ny: *secret", "application/yaml"
    elif damage == "tag":
        raw, content_type = b"x: !!python/object:untrusted {}", "application/yaml"
    elif damage == "oversize":
        raw = b" " * (MAX_DOCUMENT_BYTES + 1)
    elif damage == "nan":
        raw = b'{"private-value":NaN}'
    elif damage == "version":
        raw = json.dumps(source("3.1.0")).encode()
    elif damage == "content-type":
        content_type = "text/html"
    else:
        status = 302
    calls = []

    def receiver(request):
        calls.append(request)
        return httpx.Response(
            status,
            content=raw,
            headers={"Content-Type": content_type, "Location": "https://forbidden.test"},
        )

    with pytest.raises(ToolError) as rejected:
        await fetch_validate_description(
            HttpClient(transport=httpx.MockTransport(receiver)), {"endpoint": DOCUMENT}, timeout=1
        )
    assert "secret-marker" not in str(rejected.value) and rejected.value.details == {
        "reason": "description_fetch_failed"
    }
    assert [str(request.url) for request in calls] == [DOCUMENT]


@pytest.mark.asyncio
@pytest.mark.parametrize("opaque", ["context", "transport", "insecure"])
async def test_public_description_rejects_opaque_or_insecure_transport_before_io(opaque):
    calls = []
    client = HttpClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    if opaque == "context":
        client.verify_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    elif opaque == "transport":
        client.transport = httpx.AsyncHTTPTransport()
    else:
        client.verify_tls = False
    try:
        with pytest.raises(ToolError, match="unavailable or invalid"):
            await fetch_validate_description(client, {"endpoint": DOCUMENT}, timeout=1)
        assert calls == []
    finally:
        await client.transport.aclose()


@pytest.mark.parametrize("version", ["3.0.3", "3.2.0"])
def test_server_parameter_overrides_relative_base_and_local_reference_are_captured(version):
    document = source(version)
    document["servers"] = [{"url": "https://ignored.test"}]
    path = document["paths"]["/items/{id}"]
    path["servers"] = [{"url": "https://also-ignored.test"}]
    path["parameters"] = [
        {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
    ]
    document["components"] = {"parameters": {"Id": operation(document)["parameters"][0]}}
    operation(document)["parameters"] = [{"$ref": "#/components/parameters/Id"}]
    operation(document)["servers"] = [
        {"url": "/{stage}", "variables": {"stage": {"default": "v2", "enum": ["v2"]}}}
    ]
    if version == "3.2.0":
        document["$self"] = "https://selected-base.test/spec.json"
    selected = selection(document)
    request = prepare_request(selected, {"id": "a/b ?"})
    expected_host = "selected-base.test" if version == "3.2.0" else "description.test"
    assert request.endpoint == f"https://{expected_host}/v2/items/a%2Fb%20%3F"
    assert OpenApiOperationSelection.from_value(selected.as_value()) == selected
    assert replace(selected, remaining_timeout=0.25).as_value() == selected.as_value()


@pytest.mark.parametrize("version", ["3.0.3", "3.2.0"])
@pytest.mark.parametrize("name", ["Id~", "Id~2", "Id~10~"])
def test_malformed_reference_escape_cannot_select_an_existing_parameter(version, name):
    document = source(version)
    parameter = operation(document)["parameters"][0]
    document["components"] = {"parameters": {name: parameter}}
    operation(document)["parameters"] = [{"$ref": "#/components/parameters/" + name}]
    with pytest.raises(ToolError) as rejected:
        selection(document)
    assert rejected.value.details == {"reason": "invalid_reference_target"}


@pytest.mark.parametrize("version", ["3.0.3", "3.2.0"])
@pytest.mark.parametrize("index", ["-1", "+0", "00", " 0", "0 ", "٠", "-"])
def test_noncanonical_reference_array_index_cannot_select_an_existing_parameter(version, index):
    document = source(version)
    document["x-parameters"] = operation(document)["parameters"]
    operation(document)["parameters"] = [{"$ref": "#/x-parameters/" + index}]
    with pytest.raises(ToolError) as rejected:
        selection(document)
    assert rejected.value.details == {"reason": "invalid_reference_target"}


@pytest.mark.parametrize("version", ["3.0.3", "3.2.0"])
@pytest.mark.parametrize(
    "name,token", [("Id/part", "Id~1part"), ("Id~part", "Id~0part"), ("Id~1", "Id~01")]
)
def test_escaped_reference_tokens_keep_the_exact_parameter_binding(version, name, token):
    document = source(version)
    parameter = operation(document)["parameters"][0]
    document["components"] = {"parameters": {name: parameter}}
    operation(document)["parameters"] = [{"$ref": "#/components/parameters/" + token}]
    selected = selection(document)
    assert prepare_request(selected, {"id": "original/value"}).endpoint == (
        "https://api.test/v1/items/original%2Fvalue"
    )
    assert OpenApiOperationSelection.from_value(selected.as_value()) == selected


@pytest.mark.parametrize("version", ["3.0.3", "3.2.0"])
@pytest.mark.parametrize("container", ["array", "object"])
def test_reference_index_zero_preserves_array_and_numeric_object_key_bindings(version, container):
    document = source(version)
    parameter = operation(document)["parameters"][0]
    document["x-parameters"] = [parameter] if container == "array" else {"0": parameter}
    operation(document)["parameters"] = [{"$ref": "#/x-parameters/0"}]
    assert prepare_request(selection(document), {"id": "original"}).endpoint == (
        "https://api.test/v1/items/original"
    )


@pytest.mark.parametrize("version", ["3.0.3", "3.2.0"])
@pytest.mark.parametrize(
    "name,token",
    [
        ("Id entry", "Id%20entry"),
        ("Id%entry", "Id%25entry"),
        ("Idé", "Id%C3%A9"),
        ("Id~1", "Id%7E01"),
        ("Id%20entry", "Id%2520entry"),
    ],
)
def test_reference_uri_fragment_decodes_once_before_selecting_parameter(version, name, token):
    document = source(version)
    parameter = operation(document)["parameters"][0]
    decoy = {**parameter, "schema": {"type": "integer"}}
    document["x-parameters"] = {name: parameter, token: decoy}
    operation(document)["parameters"] = [{"$ref": "#/x-parameters/" + token}]
    selected = selection(document)
    assert prepare_request(selected, {"id": "original/value"}).endpoint == (
        "https://api.test/v1/items/original%2Fvalue"
    )
    assert OpenApiOperationSelection.from_value(selected.as_value()) == selected


@pytest.mark.parametrize("token", ["Id%", "Id%2", "Id%GG", "Id%FF", "Id%C3", "Id%7E2"])
def test_invalid_reference_uri_encoding_rejects_even_when_raw_key_exists(token):
    document = source()
    document["x-parameters"] = {token: operation(document)["parameters"][0]}
    operation(document)["parameters"] = [{"$ref": "#/x-parameters/" + token}]
    with pytest.raises(ToolError) as rejected:
        selection(document)
    assert rejected.value.details == {"reason": "invalid_reference_target"}


@pytest.mark.parametrize("token", ["%30", "nested%2FId"])
def test_reference_uri_decoding_precedes_pointer_token_and_array_resolution(token):
    document = source()
    parameter = operation(document)["parameters"][0]
    document["x-parameters"] = [parameter] if token == "%30" else {"nested": {"Id": parameter}}
    operation(document)["parameters"] = [{"$ref": "#/x-parameters/" + token}]
    assert prepare_request(selection(document), {"id": "original"}).endpoint.endswith("/original")


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "external-ref",
        "recursive-schema",
        "body",
        "ambiguous-name",
        "duplicate-parameter",
        "style",
        "schema",
        "variable",
        "path",
        "security",
    ],
)
def test_unqualified_operation_profiles_fail_selection_without_any_business_io(damage):
    document = source()
    selected = operation(document)
    identifier = "getItem"
    if damage == "missing":
        identifier = "missing"
    elif damage == "duplicate":
        document["paths"]["/other"] = {"get": copy.deepcopy(selected)}
    elif damage == "external-ref":
        selected["parameters"] = [{"$ref": "https://untrusted.test/ref.json"}]
    elif damage == "recursive-schema":
        document["components"] = {
            "schemas": {
                "Loop": {
                    "type": "object",
                    "properties": {"next": {"$ref": "#/components/schemas/Loop"}},
                }
            }
        }
        selected["parameters"][0]["schema"] = {"$ref": "#/components/schemas/Loop"}
    elif damage == "body":
        selected["requestBody"] = {"content": {"application/json": {"schema": {"type": "object"}}}}
    elif damage == "ambiguous-name":
        selected["parameters"].append({"name": "id", "in": "query", "schema": {"type": "string"}})
    elif damage == "duplicate-parameter":
        selected["parameters"].append(copy.deepcopy(selected["parameters"][0]))
    elif damage == "style":
        selected["parameters"][0]["style"] = "matrix"
    elif damage == "schema":
        selected["parameters"][0]["schema"] = {"type": "string", "pattern": "(a+)+$"}
    elif damage == "variable":
        selected["servers"] = [
            {
                "url": "https://{host}/v1",
                "variables": {"host": {"default": "bad.test", "enum": ["safe.test"]}},
            }
        ]
    elif damage == "path":
        selected["parameters"] = []
    else:
        document["components"] = {"securitySchemes": {"basic": {"type": "http", "scheme": "basic"}}}
        selected["security"] = [{"basic": []}]
    with pytest.raises(ToolError):
        select_operation(OpenApiDescription.capture(DOCUMENT, document), identifier)


@pytest.mark.parametrize(
    "field",
    [
        "document_digest",
        "document_json",
        "method",
        "endpoint",
        "parameters_json",
        "security_json",
        "extra",
    ],
)
def test_retained_selection_requires_exact_original_document_and_derived_binding(field):
    value = selection().as_value()
    if field == "extra":
        value["trusted"] = True
    else:
        value[field] = "forged"
    with pytest.raises(ToolError):
        OpenApiOperationSelection.from_value(value)


def test_path_query_header_cookie_scalar_array_object_and_null_codecs():
    document = source("3.2.0")
    operation(document)["parameters"].extend(
        [
            {
                "name": "tags",
                "in": "query",
                "schema": {"type": "array", "items": {"type": "string"}},
            },
            {
                "name": "filter",
                "in": "query",
                "schema": {"type": "object", "properties": {"active": {"type": "boolean"}}},
            },
            {
                "name": "meta",
                "in": "header",
                "explode": True,
                "schema": {"type": "object", "properties": {"size": {"type": "integer"}}},
            },
            {"name": "session", "in": "cookie", "schema": {"type": "string"}},
            {"name": "nil", "in": "query", "schema": {"type": ["string", "null"]}},
        ]
    )
    request = prepare_request(
        selection(document),
        {
            "id": "a,b/",
            "tags": ["one", "two"],
            "filter": {"active": True},
            "meta": {"size": 2},
            "session": "a b;",
            "nil": None,
        },
    )
    assert request.endpoint == "https://api.test/v1/items/a%2Cb%2F"
    assert request.query == (("tags", "one"), ("tags", "two"), ("active", "true"), ("nil", ""))
    assert request.headers == (("meta", "size=2"), ("Cookie", "session=a%20b%3B"))


@pytest.mark.parametrize("version", ["3.0.3", "3.2.0"])
def test_reviewed_nullable_and_exclusive_numeric_schema_semantics(version):
    document = source(version)
    schema = (
        {"type": "number", "minimum": 0, "exclusiveMinimum": True, "nullable": True}
        if version == "3.0.3"
        else {"type": ["number", "null"], "exclusiveMinimum": 0}
    )
    operation(document)["parameters"].append({"name": "rank", "in": "query", "schema": schema})
    selected = selection(document)
    assert prepare_request(selected, {"id": "42", "rank": None}).query == (("rank", ""),)
    assert prepare_request(selected, {"id": "42", "rank": 1.5}).query == (("rank", "1.5"),)
    with pytest.raises(ToolError, match="unavailable or invalid"):
        prepare_request(selected, {"id": "42", "rank": 0})


@pytest.mark.parametrize("explode", [False, True])
def test_path_object_simple_codec_keeps_structural_separator_distinct_from_value(explode):
    document = source()
    parameter = operation(document)["parameters"][0]
    parameter.update(
        explode=explode, schema={"type": "object", "properties": {"a": {"type": "string"}}}
    )
    request = prepare_request(selection(document), {"id": {"a": "x=y"}})
    assert request.endpoint == "https://api.test/v1/items/" + ("a=x%3Dy" if explode else "a,x%3Dy")


@pytest.mark.parametrize("parameters", [{}, {"id": 42}, {"id": "42", "unknown": 1}])
@pytest.mark.asyncio
async def test_invalid_parameters_reject_before_authentication_or_dispatch(parameters):
    calls = []
    client = HttpClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    with pytest.raises(ToolError):
        await call_openapi(client, payload(parameters=parameters), selection=selection(), timeout=1)
    assert calls == []


@pytest.mark.parametrize("output", ["raw", "content", "response"])
@pytest.mark.asyncio
async def test_common_output_projection_and_operation_key_are_preserved(output):
    calls = []

    def receiver(request):
        calls.append(request)
        return httpx.Response(200, content=b"null", headers={"Content-Type": "application/json"})

    result = await call_openapi(
        HttpClient(transport=httpx.MockTransport(receiver)),
        payload(output=output),
        selection=selection(),
        timeout=1,
        operation_id="original-logical-key",
    )
    expected = base64.b64encode(b"null").decode()
    if output == "raw":
        assert result == expected
    elif output == "content":
        assert result is None
    else:
        assert result["body"] is None
    assert len(calls) == 1 and str(calls[0].url) == TARGET
    assert (
        calls[0].headers["Idempotency-Key"]
        == calls[0].headers["X-OWA-Operation-ID"]
        == "original-logical-key"
    )


@pytest.mark.parametrize("damage", ["grant", "binding", "requirement", "ambient-cert", "plaintext"])
@pytest.mark.asyncio
async def test_credential_authority_is_checked_before_secret_resolution(damage, monkeypatch):
    monkeypatch.delenv("OPENAPI_CLIENT_TOKEN", raising=False)
    security = SecurityConfig.model_validate(
        {"profiles": {"named": {"type": "bearer", "token": {"from_env": "OPENAPI_CLIENT_TOKEN"}}}}
    )
    auth = ProfileAuthentication(security, "named")
    document = source()
    if damage == "plaintext":
        document["servers"] = [{"url": "http://api.test/v1"}]
    if damage == "requirement":
        document["components"] = {
            "securitySchemes": {"key": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}}
        }
        operation(document)["security"] = [{"key": []}]
    selected = selection(document, None if damage == "binding" else "named")
    calls = []
    client = HttpClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
    if damage == "ambient-cert":
        client.client_cert = ("never-open-cert", "never-open-key")
        selected, auth = selection(), None
    with pytest.raises(ToolError) as error:
        await call_openapi(
            client,
            payload(),
            selection=selected,
            timeout=1,
            authentication=auth,
            credential_endpoints={} if damage == "grant" else {"named": (TARGET,)},
        )
    assert calls == [] and "OPENAPI_CLIENT_TOKEN" not in str(error.value)


def _combined_security_document(requirements):
    document = source()
    document["components"] = {
        "securitySchemes": {
            "bearer": {"type": "http", "scheme": "bearer"},
            "key": {"type": "apiKey", "in": "header", "name": "X-Required-Key"},
        }
    }
    operation(document)["security"] = requirements
    return document


@pytest.mark.asyncio
async def test_openapi_security_requirement_uses_and_within_each_or_alternative(
    monkeypatch,
):
    monkeypatch.delenv("OPENAPI_AND_BEARER", raising=False)
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "named": {
                    "type": "bearer",
                    "token": {"from_env": "OPENAPI_AND_BEARER"},
                }
            }
        }
    )
    authentication = ProfileAuthentication(security, "named")
    calls = []
    resolutions = []

    def track_secret_resolution(reference):
        resolutions.append(reference)
        return "must-not-be-resolved-for-unsatisfied-requirement"

    monkeypatch.setattr(security_module, "resolve_secret", track_secret_resolution)
    selected = selection(_combined_security_document([{"bearer": [], "key": []}]), "named")

    with pytest.raises(ToolError) as rejected:
        await call_openapi(
            HttpClient(transport=httpx.MockTransport(lambda request: calls.append(request))),
            payload(),
            selection=selected,
            timeout=1,
            authentication=authentication,
            credential_endpoints={"named": (TARGET,)},
        )

    assert rejected.value.details["reason"] == "security_requirement_unsatisfied"
    assert resolutions == [] and calls == []


@pytest.mark.asyncio
async def test_openapi_fully_satisfied_security_alternative_passes_with_and_alternative_present(
    monkeypatch,
):
    monkeypatch.setenv("OPENAPI_OR_BEARER", "selected-deployment-token")
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "named": {
                    "type": "bearer",
                    "token": {"from_env": "OPENAPI_OR_BEARER"},
                }
            }
        }
    )
    authentication = ProfileAuthentication(security, "named")
    calls = []

    def receiver(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer selected-deployment-token"
        return httpx.Response(200, json={"id": "42"})

    selected = selection(
        _combined_security_document([{"bearer": [], "key": []}, {"bearer": []}]),
        "named",
    )
    result = await call_openapi(
        HttpClient(transport=httpx.MockTransport(receiver)),
        payload(),
        selection=selected,
        timeout=1,
        authentication=authentication,
        credential_endpoints={"named": (TARGET,)},
    )

    assert result == {"id": "42"} and len(calls) == 1


@pytest.mark.asyncio
async def test_named_api_key_endpoint_grant_and_projection_redaction(monkeypatch):
    monkeypatch.setenv("OPENAPI_CLIENT_KEY", "private-key-value")
    security = SecurityConfig.model_validate(
        {
            "profiles": {
                "named": {
                    "type": "api_key",
                    "key": {"from_env": "OPENAPI_CLIENT_KEY"},
                    "header": "X-Private-Key",
                }
            }
        }
    )
    auth = ProfileAuthentication(security, "named")
    document = source()
    document["components"] = {
        "securitySchemes": {"key": {"type": "apiKey", "in": "header", "name": "X-Private-Key"}}
    }
    operation(document)["security"] = [{"key": []}]
    selected = selection(document, "named")
    calls = []

    def receiver(request):
        calls.append(request)
        assert request.headers["X-Private-Key"] == "private-key-value"
        return httpx.Response(200, json={"id": "42"})

    result = await call_openapi(
        HttpClient(transport=httpx.MockTransport(receiver)),
        payload(output="response"),
        selection=selected,
        timeout=1,
        authentication=auth,
        credential_endpoints={"named": (TARGET,)},
    )
    assert result["body"] == {"id": "42"} and len(calls) == 1
    assert "private-key-value" not in json.dumps(result) and "private-key-value" not in json.dumps(
        selected.as_value()
    )


@pytest.mark.asyncio
async def test_explicit_oauth_client_credentials_satisfy_declared_operation_scope(monkeypatch):
    token_url = "https://issuer.test/token"
    monkeypatch.setenv("OPENAPI_OAUTH_ID", "named-client")
    monkeypatch.setenv("OPENAPI_OAUTH_SECRET", "private-oauth-client-secret")
    document = source()
    document["components"] = {
        "securitySchemes": {
            "oauth": {
                "type": "oauth2",
                "flows": {
                    "clientCredentials": {
                        "tokenUrl": token_url,
                        "scopes": {"items:read": "Read items"},
                    }
                },
            }
        }
    }
    operation(document)["security"] = [{"oauth": ["items:read"]}]
    calls = []

    def receiver(request):
        calls.append(request)
        if str(request.url) == token_url:
            assert "authorization" not in request.headers
            return httpx.Response(
                200,
                json={
                    "access_token": "private-issued-token",
                    "token_type": "Bearer",
                    "expires_in": 60,
                    "scope": "items:read",
                },
            )
        assert (
            str(request.url) == TARGET
            and request.headers["Authorization"] == "Bearer private-issued-token"
        )
        return httpx.Response(200, json={"id": "42"})

    client = HttpClient(transport=httpx.MockTransport(receiver))
    auth = ProfileAuthentication(
        SecurityConfig.model_validate(
            {
                "profiles": {
                    "named": {
                        "type": "oauth2_client_credentials",
                        "token_url": token_url,
                        "issuer": "https://issuer.test",
                        "resource": TARGET,
                        "allowed_resource_endpoints": [TARGET],
                        "scopes": ["items:read"],
                        "client_id": {"from_env": "OPENAPI_OAUTH_ID"},
                        "client_secret": {"from_env": "OPENAPI_OAUTH_SECRET"},
                    }
                }
            }
        ),
        "named",
        token_http=client,
    )
    selected = selection(document, "named")
    result = await call_openapi(
        client, payload(), selection=selected, timeout=1, authentication=auth
    )
    assert result == {"id": "42"} and [str(request.url) for request in calls] == [token_url, TARGET]
    assert "private-issued-token" not in json.dumps(selected.as_value())
    assert "private-oauth-client-secret" not in json.dumps(selected.as_value())


@pytest.mark.asyncio
async def test_oauth_client_credentials_scope_mismatch_rejects_before_secret_resolution_or_io(
    monkeypatch,
):
    token_url = "https://issuer.test/token"
    document = source()
    document["components"] = {
        "securitySchemes": {
            "oauth": {
                "type": "oauth2",
                "flows": {
                    "clientCredentials": {
                        "tokenUrl": token_url,
                        "scopes": {"items:read": "Read items"},
                    }
                },
            }
        }
    }
    operation(document)["security"] = [{"oauth": ["items:read"]}]
    resolved = []
    calls = []

    def resolve_secret(reference):
        resolved.append(reference)
        return "must-not-be-resolved"

    def receiver(request):
        calls.append(request)
        return httpx.Response(200, json={"unexpected": True})

    monkeypatch.setattr(security_module, "resolve_secret", resolve_secret)
    client = HttpClient(transport=httpx.MockTransport(receiver))
    auth = ProfileAuthentication(
        SecurityConfig.model_validate(
            {
                "profiles": {
                    "named": {
                        "type": "oauth2_client_credentials",
                        "token_url": token_url,
                        "issuer": "https://issuer.test",
                        "resource": TARGET,
                        "allowed_resource_endpoints": [TARGET],
                        "scopes": ["items:write"],
                        "client_id": {"from_env": "OPENAPI_OAUTH_ID"},
                        "client_secret": {"from_env": "OPENAPI_OAUTH_SECRET"},
                    }
                }
            }
        ),
        "named",
        token_http=client,
    )

    with pytest.raises(ToolError) as rejected:
        await call_openapi(
            client,
            payload(),
            selection=selection(document, "named"),
            timeout=1,
            authentication=auth,
        )

    assert rejected.value.details["reason"] == "security_requirement_unsatisfied"
    assert resolved == [] and calls == []


@pytest.mark.parametrize("redirect", [False, True])
@pytest.mark.asyncio
async def test_ows_redirect_controls_status_without_forwarding_credentials(redirect, monkeypatch):
    monkeypatch.setenv("OPENAPI_REDIRECT_TOKEN", "redirect-secret")
    auth = ProfileAuthentication(
        SecurityConfig.model_validate(
            {
                "profiles": {
                    "named": {
                        "type": "bearer",
                        "token": {"from_env": "OPENAPI_REDIRECT_TOKEN"},
                    }
                }
            }
        ),
        "named",
    )
    calls = []

    def receiver(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer redirect-secret"
        return httpx.Response(302, content=b"moved", headers={"Location": "https://forbidden.test"})

    client = HttpClient(transport=httpx.MockTransport(receiver))
    if redirect:
        assert (
            await call_openapi(
                client,
                payload(redirect=True),
                selection=selection(profile="named"),
                timeout=1,
                authentication=auth,
                credential_endpoints={"named": (TARGET,)},
            )
            == "moved"
        )
    else:
        with pytest.raises(ToolError) as known:
            await call_openapi(
                client,
                payload(),
                selection=selection(profile="named"),
                timeout=1,
                authentication=auth,
                credential_endpoints={"named": (TARGET,)},
            )
        assert known.value.details["status"] == 302 and not isinstance(
            known.value, _OpenApiOutcomeUncertain
        )
    assert [str(request.url) for request in calls] == [TARGET]


@pytest.mark.parametrize("failure", ["disconnect", "oversize", "timeout"])
@pytest.mark.asyncio
async def test_failure_after_business_dispatch_is_uncertain_and_never_retried(failure):
    calls, markers = [], []

    async def receiver(request):
        calls.append(request)
        if failure == "disconnect":
            raise httpx.ReadError("private-remote-detail")
        if failure == "timeout":
            await asyncio.sleep(2)
        return httpx.Response(200, content=b"x" * 10)

    client = HttpClient(
        transport=httpx.MockTransport(receiver),
        max_response_bytes=2 if failure == "oversize" else 100,
    )
    with pytest.raises(_OpenApiOutcomeUncertain) as uncertain:
        await call_openapi(
            client,
            payload(),
            selection=selection(),
            timeout=1,
            _on_dispatch=lambda: markers.append("sent"),
        )
    assert (
        len(calls) == 1
        and markers == ["sent"]
        and "private-remote-detail" not in str(uncertain.value)
    )


@pytest.mark.asyncio
async def test_host_drain_cancellation_propagates_after_response_stream_cleanup():
    entered = asyncio.Event()
    closed, calls = [], []

    class HeldResponse(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            closed.append(True)

    def receiver(request):
        calls.append(request)
        return httpx.Response(200, stream=HeldResponse())

    running = asyncio.create_task(
        call_openapi(
            HttpClient(transport=httpx.MockTransport(receiver)),
            payload(),
            selection=selection(),
            timeout=5,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 2)
        assert len(calls) == 1 and closed == [True]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_authentication_is_known_before_dispatch(monkeypatch):
    monkeypatch.delenv("OPENAPI_CLIENT_TOKEN", raising=False)
    auth = ProfileAuthentication(
        SecurityConfig.model_validate(
            {
                "profiles": {
                    "named": {"type": "bearer", "token": {"from_env": "OPENAPI_CLIENT_TOKEN"}}
                }
            }
        ),
        "named",
    )
    calls, markers = [], []
    with pytest.raises(ToolError):
        await call_openapi(
            HttpClient(transport=httpx.MockTransport(lambda request: calls.append(request))),
            payload(),
            selection=selection(profile="named"),
            timeout=1,
            authentication=auth,
            credential_endpoints={"named": (TARGET,)},
            _on_dispatch=lambda: markers.append("sent"),
        )
    assert calls == markers == []


@pytest.mark.parametrize("reply", ["valid", "wrong-type", "duplicate-key", "wrong-media"])
@pytest.mark.asyncio
async def test_selected_json_response_schema_is_validated_before_output(reply):
    document = source()
    operation(document)["responses"]["200"]["content"] = {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            }
        }
    }
    calls = []

    def receiver(request):
        calls.append(request)
        raw = (
            b'{"id":"42"}'
            if reply in {"valid", "wrong-media"}
            else b'{"id":42}'
            if reply == "wrong-type"
            else b'{"id":"42","id":"forged"}'
        )
        return httpx.Response(
            200,
            content=raw,
            headers={
                "Content-Type": "text/plain" if reply == "wrong-media" else "application/json"
            },
        )

    client = HttpClient(transport=httpx.MockTransport(receiver))
    if reply == "valid":
        assert await call_openapi(client, payload(), selection=selection(document), timeout=1) == {
            "id": "42"
        }
    else:
        with pytest.raises(_OpenApiOutcomeUncertain):
            await call_openapi(client, payload(), selection=selection(document), timeout=1)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_actual_tcp_description_then_exact_business_route_and_cleanup():
    observed = []
    document = source()
    writers = set()

    async def peer(reader, writer):
        writers.add(writer)
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            method, path, _ = header.split(b"\r\n", 1)[0].decode().split(" ")
            observed.append((method, path))
            value = document if path == "/spec.json" else {"id": "42"}
            raw = json.dumps(value).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Connection: close\r\nContent-Length: "
                + str(len(raw)).encode()
                + b"\r\n\r\n"
                + raw
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)

    server = await asyncio.start_server(peer, "127.0.0.1", 0)
    endpoint = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    document["servers"] = [{"url": endpoint + "/v1"}]
    try:
        client = HttpClient(timeout=2, allowed_hosts={"127.0.0.1"})
        description = await fetch_validate_description(
            client, {"endpoint": endpoint + "/spec.json"}, timeout=2
        )
        result = await call_openapi(
            client, payload(), selection=select_operation(description, "getItem"), timeout=2
        )
        assert result == {"id": "42"} and observed == [
            ("GET", "/spec.json"),
            ("GET", "/v1/items/42"),
        ]
    finally:
        server.close()
        await server.wait_closed()
        for writer in tuple(writers):
            writer.close()
            await writer.wait_closed()
    assert not writers


@pytest.mark.asyncio
async def test_actual_mtls_selected_operation_uses_exact_named_endpoint_binding(
    tmp_path, monkeypatch
):
    with auth_tls_receiver(tmp_path) as server:
        certificate, key = server.certificates["client-A"]
        monkeypatch.setenv("OPENAPI_CERT", str(certificate))
        monkeypatch.setenv("OPENAPI_KEY", str(key))
        monkeypatch.setenv("OPENAPI_CA", server.ca_bundle)
        auth = ProfileAuthentication(
            SecurityConfig.model_validate(
                {
                    "profiles": {
                        "named": {
                            "type": "mtls",
                            "certificate": {"from_env": "OPENAPI_CERT"},
                            "private_key": {"from_env": "OPENAPI_KEY"},
                            "ca_bundle": {"from_env": "OPENAPI_CA"},
                        }
                    }
                }
            ),
            "named",
        )
        document = source("3.2.0")
        document["servers"] = [{"url": server.origin}]
        document["components"] = {"securitySchemes": {"certificate": {"type": "mutualTLS"}}}
        document["paths"] = {
            "/mtls": {
                "post": {
                    "operationId": "certificate",
                    "security": [{"certificate": []}],
                    "responses": {"200": {"description": "Identity"}},
                }
            }
        }
        selected = select_operation(
            OpenApiDescription.capture(DOCUMENT, document),
            "certificate",
            credential_profile="named",
        )
        result = await call_openapi(
            HttpClient(timeout=3),
            {"operationId": "certificate"},
            selection=selected,
            timeout=3,
            authentication=auth,
            credential_endpoints={"named": (server.origin + "/mtls",)},
        )
        assert result == {"identity": "client-A"} and len(server.requests) == 1
        assert server.requests[0][3] == "client-A"
        assert str(key) not in json.dumps(selected.as_value())


@pytest.mark.asyncio
async def test_actual_mtls_wrong_endpoint_grant_rejects_before_certificate_resolution(
    tmp_path, monkeypatch
):
    with auth_tls_receiver(tmp_path) as server:
        certificate, key = server.certificates["client-A"]
        monkeypatch.setenv("OPENAPI_CERT", str(certificate))
        monkeypatch.setenv("OPENAPI_KEY", str(key))
        monkeypatch.setenv("OPENAPI_CA", server.ca_bundle)
        auth = ProfileAuthentication(
            SecurityConfig.model_validate(
                {
                    "profiles": {
                        "named": {
                            "type": "mtls",
                            "certificate": {"from_env": "OPENAPI_CERT"},
                            "private_key": {"from_env": "OPENAPI_KEY"},
                            "ca_bundle": {"from_env": "OPENAPI_CA"},
                        }
                    }
                }
            ),
            "named",
        )
        prepared = []

        async def resolve_certificate(*args, **kwargs):
            prepared.append((args, kwargs))
            raise AssertionError("wrong endpoint must reject before certificate resolution")

        monkeypatch.setattr(auth, "prepare", resolve_certificate)
        document = source("3.2.0")
        document["servers"] = [{"url": server.origin}]
        document["components"] = {"securitySchemes": {"certificate": {"type": "mutualTLS"}}}
        document["paths"] = {
            "/mtls": {
                "post": {
                    "operationId": "certificate",
                    "security": [{"certificate": []}],
                    "responses": {"200": {"description": "Identity"}},
                }
            }
        }
        selected = select_operation(
            OpenApiDescription.capture(DOCUMENT, document),
            "certificate",
            credential_profile="named",
        )

        with pytest.raises(ToolError) as rejected:
            await call_openapi(
                HttpClient(timeout=3),
                {"operationId": "certificate"},
                selection=selected,
                timeout=3,
                authentication=auth,
                credential_endpoints={"named": (server.origin + "/different-path",)},
            )

        assert rejected.value.details["reason"] == "credential_endpoint_not_granted"
        assert prepared == [] and server.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        "Host",
        "HOST",
        "Content-Length",
        "Transfer-Encoding",
        "Connection",
        "Keep-Alive",
        "Proxy-Connection",
        "TE",
        "Trailer",
        "Upgrade",
        "HTTP2-Settings",
    ],
)
async def test_reserved_authority_and_framing_parameters_reject_before_named_credentials_or_io(
    monkeypatch, header
):
    monkeypatch.setenv("OPENAPI_RESERVED_TOKEN", "must-not-be-forwarded")
    auth = ProfileAuthentication(
        SecurityConfig.model_validate(
            {
                "profiles": {
                    "named": {"type": "bearer", "token": {"from_env": "OPENAPI_RESERVED_TOKEN"}}
                }
            }
        ),
        "named",
    )
    prepared = []
    original = auth.prepare

    async def observe(*args, **kwargs):
        prepared.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(auth, "prepare", observe)
    document = source()
    operation(document)["parameters"].append(
        {"name": header, "in": "header", "schema": {"type": "string"}}
    )
    requests = []

    def receiver(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "42"})

    with pytest.raises(ToolError) as failure:
        selected = selection(document, "named")
        await call_openapi(
            HttpClient(transport=httpx.MockTransport(receiver)),
            payload(parameters={"id": "42", header: "foreign-virtual-host.test"}),
            selection=selected,
            timeout=1,
            authentication=auth,
            credential_endpoints={"named": (TARGET,)},
        )
    assert failure.value.details["reason"] == "unsupported_header_parameter"
    assert prepared == [] and requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("segment", [".", ".."])
async def test_dot_path_parameter_rejects_before_exact_named_grant_credential_resolution_or_io(
    monkeypatch, segment
):
    monkeypatch.setenv("OPENAPI_DOT_TOKEN", "must-not-be-forwarded")
    auth = ProfileAuthentication(
        SecurityConfig.model_validate(
            {"profiles": {"named": {"type": "bearer", "token": {"from_env": "OPENAPI_DOT_TOKEN"}}}}
        ),
        "named",
    )
    prepared = []
    original = auth.prepare

    async def observe(*args, **kwargs):
        prepared.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(auth, "prepare", observe)
    requests = []
    client = HttpClient(transport=httpx.MockTransport(lambda request: requests.append(request)))
    with pytest.raises(ToolError) as failure:
        await call_openapi(
            client,
            payload(parameters={"id": segment}),
            selection=selection(profile="named"),
            timeout=1,
            authentication=auth,
            credential_endpoints={"named": ("https://api.test/v1/items/" + segment,)},
        )
    assert failure.value.details["reason"] == "normalization_changing_path"
    assert prepared == [] and requests == []


@pytest.mark.parametrize("route", ["server", "relative-server", "server-variable", "path", "self"])
def test_raw_description_routes_cannot_hide_dot_normalization_before_selection(route):
    document = source("3.2.0")
    if route == "server":
        document["servers"] = [{"url": "https://api.test/v1/../private"}]
    elif route == "relative-server":
        document["servers"] = [{"url": "../private"}]
    elif route == "server-variable":
        document["servers"] = [
            {
                "url": "https://api.test/{version}/private",
                "variables": {"version": {"default": ".."}},
            }
        ]
    elif route == "path":
        document["paths"] = {"/items/../private/{id}": document["paths"]["/items/{id}"]}
    else:
        document["$self"] = "../private/spec.json"
    with pytest.raises(ToolError) as failure:
        selection(document)
    assert failure.value.details["reason"] == "normalization_changing_path"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameter,encoded",
    [
        ("literal..segment", "literal..segment"),
        ("literal%2E%2E", "literal%252E%252E"),
        ("a/../b", "a%2F..%2Fb"),
    ],
)
async def test_opaque_encoded_path_values_keep_admitted_route_and_actual_wire_uri(
    parameter, encoded
):
    requests = []

    def receiver(request):
        requests.append(request)
        return httpx.Response(200, json={"id": parameter})

    result = await call_openapi(
        HttpClient(transport=httpx.MockTransport(receiver)),
        payload(parameters={"id": parameter}),
        selection=selection(),
        timeout=1,
    )
    assert result == {"id": parameter}
    assert len(requests) == 1 and str(requests[0].url) == "https://api.test/v1/items/" + encoded
