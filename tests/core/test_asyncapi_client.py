"""Pure description witnesses; no broker connection or delivery claim."""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError, replace

import pytest
import yaml
from open_workflow_agent.asyncapi_client import (
    MAX_DOCUMENT_BYTES,
    AsyncApiDescription,
    AsyncApiOperationSelection,
    prepare_message,
    prepare_subscription,
    select_operation,
    validate_inbound_message,
)
from open_workflow_agent.errors import ToolError, UnsupportedWorkflowFeature
from open_workflow_agent.workflow import compile_workflow, validate_schema

URI = "https://description.test/asyncapi.yaml"
VERSIONS = ["2.6.0", "3.0.0", "3.1.0"]


def source(version="3.1.0", mode="publish", protocol="kafka"):
    message = {
        "payload": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "headers": {
            "type": "object",
            "properties": {"trace": {"type": "string"}},
            "required": ["trace"],
            "additionalProperties": False,
        },
        "correlationId": {"location": "$message.header#/trace"},
    }
    server = {
        "protocol": protocol,
        "variables": {"port": {"default": "9092", "enum": ["9092", "9093"]}},
    }
    server["url" if version == "2.6.0" else "host"] = "broker.test:{port}"
    if protocol == "http":
        server["url" if version == "2.6.0" else "host"] = "api.test"
        server["variables"] = {}
    result = {
        "asyncapi": version,
        "info": {"title": "Finite", "version": "1.0.0"},
        "servers": {"z-first": server, "a-second": {**server}},
        "components": {"messages": {"event": message}},
    }
    bindings = (
        {"kafka": {"bindingVersion": "0.5.0", "topic": "events"}} if protocol == "kafka" else {}
    )
    if version == "2.6.0":
        result["channels"] = {
            "events": {
                mode: {"message": {"$ref": "#/components/messages/event"}},
                "bindings": bindings,
            }
        }
    else:
        result["channels"] = {
            "events": {
                "address": "events",
                "bindings": bindings,
                "messages": {"event": {"$ref": "#/components/messages/event"}},
            }
        }
        result["operations"] = {
            "process": {
                "action": "send" if mode == "publish" else "receive",
                "channel": {"$ref": "#/channels/events"},
                "messages": [{"$ref": "#/channels/events/messages/event"}],
            }
        }
    return result


def selected(document=None, *, mode="publish", **options):
    document = document or source(mode=mode)
    selector = (
        {"channel": "events"} if document["asyncapi"] == "2.6.0" else {"operation": "process"}
    )
    return select_operation(
        AsyncApiDescription.capture(URI, document), mode=mode, **selector, **options
    )


def operation(document):
    return document["channels"]["events"].get(
        "publish", document.get("operations", {}).get("process")
    )


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("encoding", ["json", "yaml"])
def test_original_version_local_message_and_first_server_survive_strict_roundtrip(
    version, encoding
):
    document = source(version)
    raw = (
        json.dumps(document).encode()
        if encoding == "json"
        else yaml.safe_dump(document, sort_keys=False).encode()
    )
    description = AsyncApiDescription.parse(URI, raw)
    selection = selected(description.document())
    assert selection.version == version
    assert selection.server_name == "z-first"
    assert json.loads(selection.server_json)["endpoint"] == "kafka://broker.test:9092"
    assert selection.action == ("publish" if version == "2.6.0" else "send")
    prepared = prepare_message(selection, {"payload": {"id": "1"}, "headers": {"trace": "trace-1"}})
    assert prepared.correlation_id == "trace-1" and prepared.message_index == 0
    assert json.loads(prepared.payload_json) == {"id": "1"}
    assert AsyncApiOperationSelection.from_value(selection.as_value()) == selection
    document["servers"]["z-first"]["protocol"] = "nats"
    assert description.document()["servers"]["z-first"]["protocol"] == "kafka"
    with pytest.raises(FrozenInstanceError):
        selection.address = "foreign"


@pytest.mark.parametrize("version", VERSIONS)
def test_explicit_server_ignores_protocol_selector_and_binds_only_declared_variables(version):
    selection = selected(
        source(version), server={"name": "a-second", "variables": {"port": "9093"}}, protocol="nats"
    )
    assert selection.server_name == "a-second"
    assert json.loads(selection.server_json)["endpoint"] == "kafka://broker.test:9093"
    with pytest.raises(ToolError):
        selected(source(version), server={"name": "a-second", "variables": {"port": "9094"}})
    with pytest.raises(ToolError):
        selected(source(version), server={"name": "a-second", "variables": {"undeclared": "9093"}})


@pytest.mark.parametrize("version", VERSIONS)
def test_channel_server_subset_and_protocol_filter_preserve_original_declaration_order(version):
    document = source(version)
    document["channels"]["events"]["servers"] = (
        ["a-second"] if version == "2.6.0" else [{"$ref": "#/servers/a-second"}]
    )
    assert selected(document, protocol="kafka").server_name == "a-second"
    with pytest.raises(ToolError):
        selected(document, server={"name": "z-first"})


@pytest.mark.parametrize("version", VERSIONS)
def test_http_binding_is_a_declared_descriptor_with_no_transport_dispatch(version):
    document = source(version, protocol="http")
    operation(document)["bindings"] = {
        "http": {
            "bindingVersion": "0.3.0",
            "method": "POST",
            "query": {"type": "object", "properties": {"id": {"type": "string"}}},
        }
    }
    selection = selected(document)
    assert json.loads(selection.bindings_json)["operation"]["http"]["method"] == "POST"
    assert json.loads(selection.server_json)["endpoint"] == "http://api.test"


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize(
    "kind,position", [("amount", "count"), ("while", "after"), ("until", "before")]
)
def test_subscription_keeps_filtered_count_condition_position_and_original_duration(
    version, kind, position
):
    selection = selected(source(version, mode="subscribe"), mode="subscribe")
    consume = {kind: 2 if kind == "amount" else "${ .payload.id != null }", "for": {"seconds": 10}}
    subscription = {
        "consume": consume,
        "filter": "${ .headers.trace == $workflow.input.trace }",
        "foreach": {
            "item": "message",
            "at": "ordinal",
            "do": [{"record": {"set": {"id": "${ .payload.id }"}}}],
        },
    }
    prepared = prepare_subscription(selection, subscription)
    assert prepared.consumption_kind == kind and prepared.condition_position == position
    assert json.loads(prepared.duration_json) == {"seconds": 10}
    assert json.loads(prepared.subscription_json) == subscription
    assert (
        validate_inbound_message(
            selection,
            {"payload": {"id": "1"}, "headers": {"trace": "trace-1"}, "correlationId": "trace-1"},
        ).correlation_id
        == "trace-1"
    )
    with pytest.raises(ToolError):
        validate_inbound_message(
            selection,
            {"payload": {"id": "1"}, "headers": {"trace": "trace-1"}, "correlationId": "foreign"},
        )


@pytest.mark.parametrize("location", ["$message.payload#/id", "$message.header#/trace"])
def test_correlation_reads_only_declared_message_pointer(location):
    document = source()
    document["components"]["messages"]["event"]["correlationId"]["location"] = location
    prepared = prepare_message(
        selected(document), {"payload": {"id": "business-1"}, "headers": {"trace": "trace-1"}}
    )
    assert prepared.correlation_id == ("business-1" if "payload" in location else "trace-1")


@pytest.mark.parametrize("version", VERSIONS)
def test_security_retains_server_and_operation_requirements_without_credential_values(version):
    document = source(version)
    scheme = {"type": "http", "scheme": "bearer"}
    document["components"]["securitySchemes"] = {"named": scheme}
    security = (
        [{"named": []}] if version == "2.6.0" else [{"$ref": "#/components/securitySchemes/named"}]
    )
    document["servers"]["z-first"]["security"] = security
    operation(document)["security"] = security
    decision = json.loads(selected(document).security_json)
    assert decision["server"] and decision["operation"]
    assert "bearer" in json.dumps(decision) and "Authorization" not in json.dumps(decision)


@pytest.mark.parametrize("version", ["2.5.0", "2.6.1", "3.0.1", "3.2.0", "4.0.0", [], None])
def test_unreviewed_versions_fail_closed(version):
    document = source()
    document["asyncapi"] = version
    with pytest.raises(ToolError):
        AsyncApiDescription.capture(URI, document)


@pytest.mark.parametrize(
    "corruption",
    [
        "digest",
        "document",
        "address",
        "action",
        "server",
        "schema",
        "selector",
        "extra-field",
        "oversize",
    ],
)
def test_saved_selection_cannot_change_original_definition_or_derived_binding(corruption):
    value = selected().as_value()
    if corruption == "digest":
        value["document_digest"] = "0" * 64
    elif corruption == "document":
        value["document_json"] = value["document_json"].replace("broker.test", "foreign.test")
    elif corruption == "address":
        value["address"] = "foreign"
    elif corruption == "action":
        value["action"] = "receive"
    elif corruption == "server":
        value["server_name"] = "a-second"
    elif corruption == "schema":
        value["messages_json"] = "[]"
    elif corruption == "selector":
        value["selector_json"] = '{"mode":"publish","mode":"subscribe"}'
    elif corruption == "extra-field":
        value["hidden_transport"] = True
    elif corruption == "oversize":
        value["selector_json"] = "x" * (MAX_DOCUMENT_BYTES + 1)
    with pytest.raises(ToolError):
        AsyncApiOperationSelection.from_value(value)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"asyncapi":"3.1.0","asyncapi":"3.0.0"}',
        b"asyncapi: 3.1.0\nasyncapi: 3.0.0\n",
        b"root: &root {}\ncopy: *root\n",
        b"root: !!python/object:builtins.object {}\n",
        b'{"bad": NaN}',
        b"1: value\n",
        b"root: .nan\n",
        b"\xff",
        b"[]",
        b"x" * (MAX_DOCUMENT_BYTES + 1),
    ],
)
def test_raw_description_rejects_duplicates_aliases_tags_nonjson_values_and_bounds(raw):
    with pytest.raises(ToolError):
        AsyncApiDescription.parse(URI, raw)


@pytest.mark.parametrize("field", ["tuple", "nonstring", "nan", "depth", "nodes", "bytes"])
def test_captured_description_strictness_precedes_canonicalization(field):
    document = source()
    if field == "tuple":
        document["extension"] = (1, 2)
    elif field == "nonstring":
        document["extension"] = {1: "value"}
    elif field == "nan":
        document["extension"] = float("nan")
    elif field == "depth":
        nested = {}
        document["extension"] = nested
        for _ in range(34):
            nested["next"] = {}
            nested = nested["next"]
    elif field == "nodes":
        document["extension"] = [0] * 10_001
    else:
        document["extension"] = "x" * MAX_DOCUMENT_BYTES
    with pytest.raises(ToolError):
        AsyncApiDescription.capture(URI, document)


@pytest.mark.parametrize(
    "mutation",
    [
        "external-message",
        "missing-message",
        "recursive-message",
        "foreign-message",
        "invalid-pointer",
        "reply",
        "trait",
        "unsupported-action",
        "wrong-mode",
        "unsupported-codec",
        "unsupported-schema-format",
        "regex-schema",
        "external-schema",
        "recursive-schema",
        "other-protocol",
        "future-binding",
        "binding-field",
        "credential-field",
    ],
)
def test_selected_unsupported_features_are_rejected_before_any_business_operation(mutation):
    document = source()
    message = document["components"]["messages"]["event"]
    if mutation == "external-message":
        document["channels"]["events"]["messages"]["event"] = {
            "$ref": "https://foreign.test/message"
        }
    elif mutation == "missing-message":
        document["channels"]["events"]["messages"]["event"] = {
            "$ref": "#/components/messages/missing"
        }
    elif mutation == "recursive-message":
        document["components"]["messages"]["event"] = {"$ref": "#/components/messages/event"}
    elif mutation == "foreign-message":
        operation(document)["messages"] = [{"$ref": "#/components/messages/event"}]
    elif mutation == "invalid-pointer":
        document["channels"]["events"]["messages"]["event"] = {
            "$ref": "#/components/messages/~3event"
        }
    elif mutation == "reply":
        operation(document)["reply"] = {"channel": {"$ref": "#/channels/events"}}
    elif mutation == "trait":
        operation(document)["traits"] = [{"$ref": "#/components/operationTraits/trait"}]
    elif mutation == "unsupported-action":
        operation(document)["action"] = "send-and-receive"
    elif mutation == "wrong-mode":
        operation(document)["action"] = "receive"
    elif mutation == "unsupported-codec":
        message["contentType"] = "application/avro"
    elif mutation == "unsupported-schema-format":
        message["payload"] = {"schemaFormat": "application/avro", "schema": {}}
    elif mutation == "regex-schema":
        message["payload"]["patternProperties"] = {".*": {}}
    elif mutation == "external-schema":
        message["payload"] = {"$ref": "https://foreign.test/schema"}
    elif mutation == "recursive-schema":
        document["components"]["schemas"] = {
            "cycle": {
                "type": "object",
                "properties": {"next": {"$ref": "#/components/schemas/cycle"}},
            }
        }
        message["payload"] = {"$ref": "#/components/schemas/cycle"}
    elif mutation == "other-protocol":
        document["servers"]["z-first"]["protocol"] = "nats"
    elif mutation == "future-binding":
        document["channels"]["events"]["bindings"]["kafka"]["bindingVersion"] = "0.6.0"
    elif mutation == "binding-field":
        document["channels"]["events"]["bindings"]["kafka"]["admin.create"] = True
    elif mutation == "credential-field":
        document["servers"]["z-first"]["security"] = [
            {"type": "userPassword", "password": "do-not-store"}
        ]
    with pytest.raises(ToolError):
        selected(document)


@pytest.mark.parametrize(
    "fault",
    [
        "payload-type",
        "headers-type",
        "extra-field",
        "missing-correlation",
        "correlation-type",
        "wrong-id-type",
    ],
)
def test_outbound_message_requires_unique_matching_schema_and_valid_correlation(fault):
    message = {"payload": {"id": "1"}, "headers": {"trace": "trace-1"}}
    if fault == "payload-type":
        message["payload"] = []
    elif fault == "headers-type":
        message["headers"] = []
    elif fault == "extra-field":
        message["operationId"] = "foreign"
    elif fault == "missing-correlation":
        message["headers"] = {}
    elif fault == "correlation-type":
        message["headers"]["trace"] = 1
    elif fault == "wrong-id-type":
        message["payload"]["id"] = 1
    with pytest.raises(ToolError):
        prepare_message(selected(), message)


def test_multi_message_schema_selects_one_and_rejects_overlap():
    document = source()
    message = document["components"]["messages"]["event"]
    message["payload"]["properties"]["id"] = {"const": "first"}
    other = copy.deepcopy(message)
    other["payload"]["properties"]["id"] = {"const": "second"}
    document["components"]["messages"]["other"] = other
    document["channels"]["events"]["messages"]["other"] = {"$ref": "#/components/messages/other"}
    operation(document).pop("messages")
    assert (
        prepare_message(
            selected(document), {"payload": {"id": "second"}, "headers": {"trace": "1"}}
        ).message_index
        == 1
    )
    other["payload"]["properties"]["id"] = {"const": "first"}
    with pytest.raises(ToolError):
        prepare_message(selected(document), {"payload": {"id": "first"}, "headers": {"trace": "1"}})


@pytest.mark.parametrize(
    "consume",
    [
        {},
        {"amount": 0},
        {"amount": True},
        {"amount": 1, "until": "${ false }"},
        {"while": "not-an-expression"},
        {"until": "${ false }", "for": {"seconds": -1}},
        {"amount": 1, "for": {"months": 1}},
    ],
)
def test_invalid_subscription_policy_does_not_create_a_transport_descriptor(consume):
    with pytest.raises(ToolError):
        prepare_subscription(
            selected(source(mode="subscribe"), mode="subscribe"), {"consume": consume}
        )


@pytest.mark.parametrize("version", VERSIONS)
def test_ows_schema_accepts_the_original_call_fields_without_private_descriptors(version):
    arguments = {
        "document": {"endpoint": URI},
        "message": {"payload": {"id": "1"}, "headers": {"trace": "1"}},
        "server": {"name": "z-first", "variables": {"port": "9092"}},
        "protocol": "kafka",
    }
    arguments["channel" if version == "2.6.0" else "operation"] = (
        "events" if version == "2.6.0" else "process"
    )
    workflow = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "pure",
            "name": "description",
            "version": "1.0.0",
        },
        "do": [{"publish": {"call": "asyncapi", "with": arguments}}],
    }
    validate_schema(workflow)
    assert workflow["do"][0]["publish"]["with"] == arguments
    assert "owa/asyncapi-operation/v1" not in json.dumps(workflow)
    # Pure description support does not activate the runtime broker capability.
    with pytest.raises(UnsupportedWorkflowFeature):
        compile_workflow(workflow)


def test_description_hash_preserves_semantically_relevant_first_server_order():
    first = source()
    second = copy.deepcopy(first)
    second["servers"] = dict(reversed(list(second["servers"].items())))
    assert (
        AsyncApiDescription.capture(URI, first).digest
        != AsyncApiDescription.capture(URI, second).digest
    )
    assert selected(first).server_name == "z-first" and selected(second).server_name == "a-second"
    with pytest.raises(ToolError):
        replace(AsyncApiDescription.capture(URI, first), digest="0" * 64).document()


@pytest.mark.parametrize("version", VERSIONS)
def test_payload_schema_local_reference_and_escaped_correlation_pointer_are_resolved(version):
    document = source(version)
    message = document["components"]["messages"]["event"]
    document["components"]["schemas"] = {"payload": message["payload"]}
    message["payload"] = {"$ref": "#/components/schemas/payload"}
    message["headers"] = {
        "type": "object",
        "properties": {"trace/id": {"type": "string"}},
        "required": ["trace/id"],
    }
    message["correlationId"]["location"] = "$message.header#/trace~1id"
    assert (
        prepare_message(
            selected(document), {"payload": {"id": "1"}, "headers": {"trace/id": "a"}}
        ).correlation_id
        == "a"
    )


def test_v2_oneof_messages_preserve_unique_message_selection():
    document = source("2.6.0")
    other = copy.deepcopy(document["components"]["messages"]["event"])
    document["components"]["messages"]["event"]["payload"]["properties"]["id"] = {"const": "a"}
    other["payload"]["properties"]["id"] = {"const": "b"}
    document["components"]["messages"]["other"] = other
    operation(document)["message"] = {
        "oneOf": [{"$ref": "#/components/messages/event"}, {"$ref": "#/components/messages/other"}]
    }
    assert (
        prepare_message(
            selected(document), {"payload": {"id": "b"}, "headers": {"trace": "a"}}
        ).message_index
        == 1
    )


@pytest.mark.parametrize("version", ["3.0.0", "3.1.0"])
def test_static_channel_parameter_default_and_multiformat_draft7_remain_bound(version):
    document = source(version)
    document["channels"]["events"]["address"] = "events.{environment}"
    document["channels"]["events"]["parameters"] = {
        "environment": {"default": "dev", "enum": ["dev", "prod"]}
    }
    message = document["components"]["messages"]["event"]
    message["payload"] = {
        "schemaFormat": "application/schema+json;version=draft-07",
        "schema": message["payload"],
    }
    selection = selected(document)
    assert selection.address == "events.dev"
    assert json.loads(
        prepare_message(selection, {"payload": {"id": "1"}, "headers": {"trace": "a"}}).payload_json
    ) == {"id": "1"}


@pytest.mark.parametrize(
    "endpoint",
    [
        "kafka://user:password@broker.test:9092",
        "https://foreign.test",
        "kafka://broker.test:70000",
        "kafka://broker.test/topic?secret=x",
        "kafka://broker.test/topic#fragment",
    ],
)
def test_server_endpoint_rejects_credentials_protocol_mismatch_invalid_port_and_query(endpoint):
    document = source("2.6.0")
    document["servers"]["z-first"]["url"] = endpoint
    with pytest.raises(ToolError):
        selected(document)


@pytest.mark.parametrize("duration", ["PT0.25S", "P1DT2H", {"milliseconds": 250}])
def test_subscription_duration_is_preserved_without_starting_or_rebasing_a_clock(duration):
    prepared = prepare_subscription(
        selected(source(mode="subscribe"), mode="subscribe"),
        {"consume": {"amount": 1, "for": duration}},
    )
    assert json.loads(prepared.duration_json) == duration
    assert not hasattr(prepared, "deadline_at")


@pytest.mark.parametrize(
    "duration", ["10", "P1M", "${ .newDuration }", {"seconds": 0.5}, {"seconds": True}]
)
def test_unreviewed_calendar_unresolved_and_nonows_duration_values_reject(duration):
    with pytest.raises(ToolError):
        prepare_subscription(
            selected(source(mode="subscribe"), mode="subscribe"),
            {"consume": {"amount": 1, "for": duration}},
        )


def test_selected_reference_budget_rejects_large_closure_but_unused_external_refs_are_not_fetched():
    document = source()
    document["components"]["schemas"] = {
        "id": {"type": "string"},
        "unused": {"$ref": "https://unused.test/schema"},
    }
    assert selected(document).address == "events"
    message = document["components"]["messages"]["event"]
    message["payload"] = {
        "type": "object",
        "properties": {str(index): {"$ref": "#/components/schemas/id"} for index in range(65)},
    }
    with pytest.raises(ToolError):
        selected(document)


@pytest.mark.parametrize(
    "security",
    [
        {"type": "httpApiKey", "in": "header"},
        {"type": "apiKey", "in": "header"},
        {"type": "http", "scheme": "digest"},
        {"type": "oauth2", "flows": {"implicit": {"authorizationUrl": "https://issuer.test"}}},
        {
            "type": "oauth2",
            "flows": {"clientCredentials": {"tokenUrl": "http://issuer.test/token"}},
        },
    ],
)
def test_security_schema_does_not_accept_missing_fields_or_unreviewed_flows(security):
    document = source()
    document["servers"]["z-first"]["security"] = [security]
    with pytest.raises(ToolError):
        selected(document)


def test_oauth_description_and_kafka_group_schema_are_preserved_without_resolving_secrets():
    document = source()
    document["servers"]["z-first"]["security"] = [
        {
            "type": "oauth2",
            "flows": {
                "clientCredentials": {
                    "tokenUrl": "https://issuer.test/token",
                    "availableScopes": {"publish": "Publish messages"},
                }
            },
            "scopes": ["publish"],
        }
    ]
    document["components"]["schemas"] = {"group": {"type": "string", "const": "readers"}}
    operation(document)["bindings"] = {
        "kafka": {"bindingVersion": "0.5.0", "groupId": {"$ref": "#/components/schemas/group"}}
    }
    selection = selected(document)
    assert json.loads(selection.bindings_json)["operation"]["kafka"]["groupId"] == {
        "type": "string",
        "const": "readers",
    }
    assert (
        "issuer.test" in selection.security_json and "client_secret" not in selection.security_json
    )
