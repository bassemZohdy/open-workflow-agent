"""Private finite A2A v1 JSON contracts backed by the pinned official SDK."""

from __future__ import annotations

import copy
import json
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from a2a.types import a2a_pb2 as types
from google.protobuf import json_format
from google.protobuf.message import Message as ProtobufMessage

from .errors import ToolError
from .expressions import validate_expression_data

A2A_PROTOCOL_VERSION = "1.0"
A2A_SDK_VERSION = "1.2.2"
MAX_VALUE_BYTES = 1_048_576
MAX_PARTS = 256
A2A_METHODS = frozenset(
    {
        "SendMessage",
        "GetTask",
        "CancelTask",
        "ListTasks",
        "GetExtendedAgentCard",
        "SendStreamingMessage",
        "SubscribeToTask",
    }
)
STREAM_METHODS = frozenset({"SendStreamingMessage", "SubscribeToTask"})
_REQUESTS: dict[str, type[ProtobufMessage]] = {
    "SendMessage": types.SendMessageRequest,
    "SendStreamingMessage": types.SendMessageRequest,
    "GetTask": types.GetTaskRequest,
    "CancelTask": types.CancelTaskRequest,
    "ListTasks": types.ListTasksRequest,
    "GetExtendedAgentCard": types.GetExtendedAgentCardRequest,
    "SubscribeToTask": types.SubscribeToTaskRequest,
}
_RESULTS: dict[str, type[ProtobufMessage]] = {
    "SendMessage": types.SendMessageResponse,
    "SendStreamingMessage": types.StreamResponse,
    "SubscribeToTask": types.StreamResponse,
    "GetTask": types.Task,
    "CancelTask": types.Task,
    "ListTasks": types.ListTasksResponse,
    "GetExtendedAgentCard": types.AgentCard,
}


def failure(reason: str) -> ToolError:
    return ToolError(
        "A2A call failed",
        details={
            "type": "https://serverlessworkflow.io/spec/1.0.0/errors/runtime",
            "reason": reason,
        },
    )


def verify_sdk() -> None:
    try:
        installed = version("a2a-sdk")
    except PackageNotFoundError:
        installed = "missing"
    if installed != A2A_SDK_VERSION:
        raise ToolError(
            "unsupported A2A SDK installation; requires a2a-sdk 1.2.2",
            details={
                "required": A2A_SDK_VERSION,
                "available": installed,
            },
        )


def _bounded(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise failure("invalid_parameters")
    try:
        validate_expression_data(value)
        if len(json.dumps(value, allow_nan=False).encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError("size")
    except (ValueError, RecursionError):
        raise failure("invalid_parameters") from None
    return copy.deepcopy(value)


def _parse(value: dict[str, Any], kind: type[ProtobufMessage]) -> ProtobufMessage:
    try:
        return json_format.ParseDict(
            value, kind(), ignore_unknown_fields=False, max_recursion_depth=64
        )
    except (ValueError, TypeError, json_format.ParseError, RecursionError):
        raise failure("invalid_parameters") from None


def _json(message: ProtobufMessage) -> dict[str, Any]:
    return dict(json_format.MessageToDict(message, preserving_proto_field_name=False))


def _identifier(value: Any) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or any(ord(c) < 32 for c in value)
    ):
        raise failure("invalid_identity")


def _parts(parts: Any) -> None:
    if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS:
        raise failure("invalid_parts")
    for part in parts:
        if not isinstance(part, dict) or len(set(part) & {"text", "data", "raw", "url"}) != 1:
            raise failure("invalid_parts")
        if "text" in part and not isinstance(part["text"], str):
            raise failure("invalid_parts")
        if "url" in part:
            _identifier(part["url"])
        if "raw" in part and not isinstance(part["raw"], str):
            raise failure("invalid_parts")


def validate_v1_message(value: Any) -> dict[str, Any]:
    raw = _bounded(value)
    _identifier(raw.get("messageId"))
    if raw.get("role") not in {"ROLE_USER", "ROLE_AGENT"}:
        raise failure("invalid_role")
    _parts(raw.get("parts"))
    for key in ("contextId", "taskId"):
        if key in raw:
            _identifier(raw[key])
    return _json(_parse(raw, types.Message))


def validate_v1_request(method: str, value: Any) -> dict[str, Any]:
    if method not in A2A_METHODS:
        raise failure("unsupported_method")
    raw = _bounded(value)
    # OWA's existing deployment-owned selector is outside the protocol DTO.
    skill = raw.pop("skillId", None)
    if skill is not None:
        _identifier(skill)
    if method in {"SendMessage", "SendStreamingMessage"}:
        raw["message"] = validate_v1_message(raw.get("message"))
        if raw["message"]["role"] != "ROLE_USER":
            raise failure("invalid_role")
        configuration = raw.get("configuration", {})
        if not isinstance(configuration, dict):
            raise failure("invalid_parameters")
        if configuration.get("taskPushNotificationConfig") is not None:
            raise failure("unsupported_push")
        if (
            "returnImmediately" in configuration
            and type(configuration["returnImmediately"]) is not bool
        ):
            raise failure("invalid_parameters")
    elif method in {"GetTask", "CancelTask", "SubscribeToTask"}:
        _identifier(raw.get("id"))
    for key in ("pageSize", "historyLength"):
        if key in raw and (type(raw[key]) is not int or raw[key] < 0 or raw[key] > 1000):
            raise failure("invalid_parameters")
    if "tenant" in raw and raw["tenant"]:
        raise failure("unsupported_tenant")
    result = _json(_parse(raw, _REQUESTS[method]))
    if skill is not None:
        result["skillId"] = skill
    return result


def _status(value: Any) -> None:
    if not isinstance(value, dict) or value.get("state") not in {
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_REJECTED",
        "TASK_STATE_AUTH_REQUIRED",
    }:
        raise failure("invalid_result")
    if "message" in value:
        validate_v1_message(value["message"])


def _task(value: Any) -> None:
    if not isinstance(value, dict):
        raise failure("invalid_result")
    _identifier(value.get("id"))
    _identifier(value.get("contextId"))
    _status(value.get("status"))
    history = value.get("history", [])
    if not isinstance(history, list) or len(history) > 1000:
        raise failure("invalid_result")
    for message in history:
        validate_v1_message(message)
    artifacts = value.get("artifacts", [])
    if not isinstance(artifacts, list) or len(artifacts) > 256:
        raise failure("invalid_result")
    for artifact in artifacts:
        _artifact(artifact)


def _artifact(value: Any) -> None:
    if not isinstance(value, dict):
        raise failure("invalid_result")
    _identifier(value.get("artifactId"))
    _parts(value.get("parts"))


def validate_v1_card(value: Any) -> dict[str, Any]:
    raw = _bounded(value)
    for key in ("name", "description", "version"):
        _identifier(raw.get(key))
    interfaces = raw.get("supportedInterfaces")
    if not isinstance(interfaces, list) or not 1 <= len(interfaces) <= 32:
        raise failure("invalid_card")
    for interface in interfaces:
        if not isinstance(interface, dict):
            raise failure("invalid_card")
        for key in ("url", "protocolBinding", "protocolVersion"):
            _identifier(interface.get(key))
    for key in ("defaultInputModes", "defaultOutputModes"):
        modes = raw.get(key)
        if (
            not isinstance(modes, list)
            or not 1 <= len(modes) <= 32
            or any(not isinstance(mode, str) or not mode for mode in modes)
        ):
            raise failure("invalid_card")
    skills = raw.get("skills")
    if not isinstance(skills, list) or not 1 <= len(skills) <= 256:
        raise failure("invalid_card")
    identifiers = []
    for skill in skills:
        if not isinstance(skill, dict):
            raise failure("invalid_card")
        for key in ("id", "name", "description"):
            _identifier(skill.get(key))
        identifiers.append(skill["id"])
    if len(set(identifiers)) != len(identifiers):
        raise failure("invalid_card")
    return _json(_parse(raw, types.AgentCard))


def validate_v1_result(method: str, value: Any) -> dict[str, Any]:
    if method not in A2A_METHODS:
        raise failure("unsupported_method")
    raw = _bounded(value)
    if method == "SendMessage" or method in STREAM_METHODS:
        alternatives = {"task", "message"} | (
            {"statusUpdate", "artifactUpdate"} if method in STREAM_METHODS else set()
        )
        selected = set(raw) & alternatives
        if len(selected) != 1:
            raise failure("invalid_result")
        key = next(iter(selected))
        if key == "task":
            _task(raw[key])
        elif key == "message":
            validate_v1_message(raw[key])
        else:
            event = raw[key]
            if not isinstance(event, dict):
                raise failure("invalid_result")
            _identifier(event.get("taskId"))
            _identifier(event.get("contextId"))
            if key == "statusUpdate":
                _status(event.get("status"))
            else:
                _artifact(event.get("artifact"))
    elif method in {"GetTask", "CancelTask"}:
        _task(raw)
    elif method == "ListTasks":
        tasks = raw.get("tasks", [])
        if not isinstance(tasks, list) or len(tasks) > 1000:
            raise failure("invalid_result")
        for task in tasks:
            _task(task)
    else:
        return validate_v1_card(raw)
    return _json(_parse(raw, _RESULTS[method]))
