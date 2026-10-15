"""Bounded A2A v1 DTO validation and SDK serialization behavior."""

from __future__ import annotations

import pytest
from _a2a_sdk_fixture import peer_card
from open_workflow_agent import _a2a_types
from open_workflow_agent.errors import ToolError


def _message(**overrides: object) -> dict[str, object]:
    return {
        "messageId": "message-1",
        "role": "ROLE_USER",
        "parts": [{"text": "hello", "mediaType": "text/plain"}],
        **overrides,
    }


def _task(**overrides: object) -> dict[str, object]:
    return {
        "id": "task-1",
        "contextId": "context-1",
        "status": {"state": "TASK_STATE_COMPLETED"},
        **overrides,
    }


def _assert_reason(call, reason: str) -> None:
    with pytest.raises(ToolError) as caught:
        call()
    assert caught.value.details["reason"] == reason


def test_message_round_trips_sdk_json_and_copies_caller_data() -> None:
    value = _message(parts=[{"data": {"answer": 42}, "mediaType": "application/json"}])
    validated = _a2a_types.validate_v1_message(value)
    value["parts"][0]["data"]["answer"] = 0

    assert validated["messageId"] == "message-1"
    assert validated["parts"] == [{"data": {"answer": 42}, "mediaType": "application/json"}]


def test_message_preserves_optional_task_and_context_identity() -> None:
    validated = _a2a_types.validate_v1_message(
        _message(contextId="context-1", taskId="task-1", role="ROLE_AGENT")
    )
    assert validated["contextId"] == "context-1"
    assert validated["taskId"] == "task-1"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (None, "invalid_parameters"),
        ({"payload": "x" * (_a2a_types.MAX_VALUE_BYTES + 1)}, "invalid_parameters"),
        (_message(messageId="bad\nidentity"), "invalid_identity"),
        (_message(messageId="x" * 1025), "invalid_identity"),
        (_message(parts=[]), "invalid_parts"),
        (_message(parts=[{"text": "a", "url": "https://file.test/a"}]), "invalid_parts"),
        (_message(parts=[{"text": 4}]), "invalid_parts"),
        (_message(parts=[{"raw": 4}]), "invalid_parts"),
        (_message(parts=[{"url": ""}]), "invalid_identity"),
        (_message(unrecognizedProtocolField="secret"), "invalid_parameters"),
    ],
)
def test_message_rejects_invalid_values_with_stable_reasons(value, reason: str) -> None:
    _assert_reason(lambda: _a2a_types.validate_v1_message(value), reason)


def test_request_normalizes_methods_and_preserves_only_deployment_skill_selector() -> None:
    request = _a2a_types.validate_v1_request(
        "SendMessage",
        {"message": _message(), "skillId": "echo", "configuration": {"returnImmediately": True}},
    )
    assert request["message"]["messageId"] == "message-1"
    assert request["skillId"] == "echo"
    assert request["configuration"]["returnImmediately"] is True


@pytest.mark.parametrize(
    ("method", "params", "reason"),
    [
        ("NoSuchMethod", {}, "unsupported_method"),
        ("SendMessage", {"message": _message(role="ROLE_AGENT")}, "invalid_role"),
        (
            "SendMessage",
            {"message": _message(), "configuration": {"taskPushNotificationConfig": {}}},
            "unsupported_push",
        ),
        ("SendMessage", {"message": _message(), "configuration": []}, "invalid_parameters"),
        (
            "SendMessage",
            {"message": _message(), "configuration": {"returnImmediately": "yes"}},
            "invalid_parameters",
        ),
        ("GetTask", {"id": "task-1", "pageSize": True}, "invalid_parameters"),
        ("ListTasks", {"pageSize": 1001}, "invalid_parameters"),
        ("GetTask", {"id": "task-1", "tenant": "tenant-a"}, "unsupported_tenant"),
        ("GetTask", {"id": "task-1", "skillId": ""}, "invalid_identity"),
    ],
)
def test_request_rejects_unsupported_or_malformed_parameters(method, params, reason: str) -> None:
    _assert_reason(lambda: _a2a_types.validate_v1_request(method, params), reason)


@pytest.mark.parametrize(
    ("method", "params", "expected_field"),
    [
        ("GetTask", {"id": "task-1", "historyLength": 0}, "id"),
        ("CancelTask", {"id": "task-1"}, "id"),
        ("SubscribeToTask", {"id": "task-1"}, "id"),
        ("ListTasks", {"pageSize": 20}, "pageSize"),
        ("GetExtendedAgentCard", {}, None),
        ("SendStreamingMessage", {"message": _message()}, "message"),
    ],
)
def test_supported_requests_parse_through_pinned_sdk(method, params, expected_field) -> None:
    parsed = _a2a_types.validate_v1_request(method, params)
    if expected_field:
        assert expected_field in parsed


def test_card_round_trips_sdk_json_and_requires_unique_skills() -> None:
    card = peer_card("https://agent.test/a2a")
    value = _a2a_types.json_format.MessageToDict(card)
    assert _a2a_types.validate_v1_card(value)["name"] == "official-a2a-peer"

    value["skills"].append(dict(value["skills"][0]))
    _assert_reason(lambda: _a2a_types.validate_v1_card(value), "invalid_card")


@pytest.mark.parametrize(
    "card",
    [
        {
            "name": "peer",
            "description": "peer",
            "version": "1",
            "supportedInterfaces": [],
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [{"id": "echo", "name": "Echo", "description": "echo"}],
        },
        {
            "name": "peer",
            "description": "peer",
            "version": "1",
            "supportedInterfaces": [
                {"url": "https://peer.test", "protocolBinding": "JSONRPC", "protocolVersion": "1"}
            ],
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        },
        {
            "name": "peer",
            "description": "peer",
            "version": "1",
            "supportedInterfaces": [
                {"url": "https://peer.test", "protocolBinding": "JSONRPC", "protocolVersion": "1"}
            ],
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [None],
        },
        {
            "name": "peer",
            "description": "peer",
            "version": "1",
            "supportedInterfaces": [None],
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [{"id": "echo", "name": "Echo", "description": "echo"}],
        },
        {
            "name": "peer",
            "description": "peer",
            "version": "1",
            "supportedInterfaces": [
                {"url": "https://peer.test", "protocolBinding": "JSONRPC", "protocolVersion": "1"}
            ],
            "defaultInputModes": [],
            "defaultOutputModes": ["text/plain"],
            "skills": [{"id": "echo", "name": "Echo", "description": "echo"}],
        },
    ],
)
def test_card_rejects_missing_or_invalid_required_sections(card) -> None:
    _assert_reason(lambda: _a2a_types.validate_v1_card(card), "invalid_card")


@pytest.mark.parametrize(
    ("method", "result", "reason"),
    [
        ("bad-method", {}, "unsupported_method"),
        (
            "SendMessage",
            {"task": _task(), "message": _message(role="ROLE_AGENT")},
            "invalid_result",
        ),
        ("SendStreamingMessage", {"artifactUpdate": None}, "invalid_result"),
        (
            "SendStreamingMessage",
            {"artifactUpdate": {"taskId": "t", "contextId": "c"}},
            "invalid_result",
        ),
        ("ListTasks", {"tasks": None}, "invalid_result"),
        ("ListTasks", {"tasks": [None]}, "invalid_result"),
        ("ListTasks", {"tasks": [_task()] * 1001}, "invalid_result"),
        ("GetTask", _task(artifacts=[None]), "invalid_result"),
        ("GetTask", _task(history=[None]), "invalid_parameters"),
        ("GetTask", _task(history="invalid"), "invalid_result"),
        (
            "GetTask",
            _task(artifacts=[{"artifactId": "a", "parts": [{"text": "ok"}]}] * 257),
            "invalid_result",
        ),
        ("GetTask", _task(artifacts=[{"artifactId": "a", "parts": []}]), "invalid_parts"),
    ],
)
def test_result_rejects_malformed_union_task_and_artifact(method, result, reason: str) -> None:
    _assert_reason(lambda: _a2a_types.validate_v1_result(method, result), reason)


def test_result_serializes_valid_streaming_events_and_task_lists() -> None:
    status = {
        "statusUpdate": {
            "taskId": "task-1",
            "contextId": "context-1",
            "status": {"state": "TASK_STATE_WORKING"},
        }
    }
    artifact = {
        "artifactUpdate": {
            "taskId": "task-1",
            "contextId": "context-1",
            "artifact": {"artifactId": "result", "parts": [{"text": "ok"}]},
        }
    }
    assert "statusUpdate" in _a2a_types.validate_v1_result("SendStreamingMessage", status)
    assert "artifactUpdate" in _a2a_types.validate_v1_result("SubscribeToTask", artifact)
    assert (
        _a2a_types.validate_v1_result("ListTasks", {"tasks": [_task()]})["tasks"][0]["id"]
        == "task-1"
    )
    assert _a2a_types.validate_v1_result("SendMessage", {"task": _task()})["task"]["id"] == (
        "task-1"
    )
    assert (
        _a2a_types.validate_v1_result("SendMessage", {"message": _message(role="ROLE_AGENT")})[
            "message"
        ]["role"]
        == "ROLE_AGENT"
    )

    card = _a2a_types.json_format.MessageToDict(peer_card("https://agent.test/a2a"))
    assert _a2a_types.validate_v1_result("GetExtendedAgentCard", card)["name"] == (
        "official-a2a-peer"
    )


def test_sdk_pin_failures_are_explicit(monkeypatch) -> None:
    def missing(_name: str) -> str:
        raise _a2a_types.PackageNotFoundError

    monkeypatch.setattr(_a2a_types, "version", missing)
    with pytest.raises(ToolError, match="requires a2a-sdk 1.2.2"):
        _a2a_types.verify_sdk()

    monkeypatch.setattr(_a2a_types, "version", lambda _name: "9.9.9")
    with pytest.raises(ToolError, match="requires a2a-sdk 1.2.2"):
        _a2a_types.verify_sdk()
