"""Finite common model-response codec, independent of any execution engine.

This is an independently written adapter for the documented LiteLLM completion
message shape. It does not execute tools or assert provider delivery semantics.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import ExpressionError, ModelError
from .expressions import validate_expression_data

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}\Z")


def _invalid(reason: str) -> ModelError:
    return ModelError(
        "model response is outside the finite adapter profile", details={"reason": reason}
    )


def bounded_model_json(value: Any, *, limit: int) -> str:
    try:
        pending = [(value, 0)]
        nodes = 0
        string_bytes = 0
        while pending:
            item, depth = pending.pop()
            nodes += 1
            if nodes > 65_536 or depth > 32:
                raise _invalid("data_limit")
            if type(item) is str:
                if len(item) > limit:
                    raise _invalid("data_limit")
                string_bytes += len(item.encode("utf-8"))
            elif type(item) is dict:
                if len(item) > 65_536:
                    raise _invalid("data_limit")
                pending.extend((key, depth + 1) for key in item)
                pending.extend((nested, depth + 1) for nested in item.values())
            elif type(item) is list:
                if len(item) > 65_536:
                    raise _invalid("data_limit")
                pending.extend((nested, depth + 1) for nested in item)
            elif item is not None and type(item) not in {bool, int, float}:
                raise _invalid("invalid_data")
            if string_bytes > limit:
                raise _invalid("data_limit")
        validate_expression_data(value)
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if len(text.encode("utf-8")) > limit:
            raise _invalid("data_limit")
        return text
    except ModelError:
        raise
    except (ExpressionError, TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid("invalid_data") from None


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in items:
        if name in result:
            raise ValueError("duplicate field")
        result[name] = value
    return result


def _constant(_value: str) -> Any:
    raise ValueError("nonfinite value")


@dataclass(frozen=True, slots=True, repr=False)
class ModelToolRequest:
    """Immutable canonical tool request; arguments remain strict finite JSON."""

    name: str
    arguments_json: str

    @classmethod
    def capture(cls, name: Any, arguments: Any, *, max_argument_bytes: int) -> ModelToolRequest:
        if type(name) is not str or not _NAME.fullmatch(name) or type(arguments) is not dict:
            raise _invalid("invalid_tool_request")
        return cls(name, bounded_model_json(arguments, limit=max_argument_bytes))

    def as_value(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": json.loads(self.arguments_json)}


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _utf8_length(value: str, reason: str) -> int:
    try:
        return len(value.encode("utf-8", "strict"))
    except UnicodeError:
        raise _invalid(reason) from None


def completion_message(
    response: Any, *, max_output_bytes: int, max_tool_calls: int, max_argument_bytes: int
) -> Any:
    """Retain compatible text/null results and structured function requests.

    Only one nonstreaming completion choice and function tools qualify. Provider
    metadata, credentials, SDK message objects and tool IDs never enter the DTO.
    """
    choices = _field(response, "choices")
    if type(choices) is not list or len(choices) != 1:
        raise _invalid("invalid_choices")
    message = _field(choices[0], "message")
    if message is None or _field(message, "role", "assistant") != "assistant":
        raise _invalid("invalid_message")
    if _field(message, "refusal") is not None:
        raise _invalid("unsupported_refusal")
    content = _field(message, "content", object())
    if content is not None and type(content) is not str:
        raise _invalid("unsupported_content")
    calls = _field(message, "tool_calls")
    legacy = _field(message, "function_call")
    if calls is not None and legacy is not None:
        raise _invalid("ambiguous_tool_requests")
    if legacy is not None:
        calls = [{"type": "function", "function": legacy}]
    if calls is None or calls == []:
        bounded_model_json(content, limit=max_output_bytes)
        return content
    if type(calls) is not list or not 1 <= len(calls) <= max_tool_calls:
        raise _invalid("tool_count_limit")
    requests = []
    identifiers: set[str] = set()
    for call in calls:
        if _field(call, "type") != "function":
            raise _invalid("unsupported_tool_type")
        identifier = _field(call, "id")
        if identifier is not None:
            if (
                type(identifier) is not str
                or not identifier
                or _utf8_length(identifier, "invalid_tool_identity") > 256
                or identifier in identifiers
                or any(ord(char) < 33 or ord(char) > 126 for char in identifier)
            ):
                raise _invalid("invalid_tool_identity")
            identifiers.add(identifier)
        function = _field(call, "function")
        arguments = _field(function, "arguments")
        if (
            type(arguments) is not str
            or _utf8_length(arguments, "argument_limit") > max_argument_bytes
        ):
            raise _invalid("argument_limit")
        try:
            parsed = json.loads(arguments, object_pairs_hook=_pairs, parse_constant=_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise _invalid("invalid_arguments") from None
        requests.append(
            ModelToolRequest.capture(
                _field(function, "name"), parsed, max_argument_bytes=max_argument_bytes
            ).as_value()
        )
    result = {"content": content, "tool_calls": requests}
    bounded_model_json(result, limit=max_output_bytes)
    return result
