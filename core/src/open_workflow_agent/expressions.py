"""Pinned jq evaluation of workflow-owned expression definitions only."""

from __future__ import annotations

import copy
import json
import math
import os
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import jq  # type: ignore[import-not-found]

from .errors import ExpressionError

ExpressionMode = Literal["strict", "loose"]
MAX_SAFE_INTEGER = 2**53 - 1

# jq's $ENV is an automatically supplied global; compile(args={"ENV": {}})
# does NOT override it. Lexical binding is required as a second line of
# protection, including inside user-defined functions and string interpolation.
_PRELUDE = """
def env: error("environment access is disabled");
def input: error("external input is disabled");
def inputs: error("external input is disabled");
def debug: error("expression diagnostics are disabled");
def debug($messages): error("expression diagnostics are disabled");
def stderr: error("expression diagnostics are disabled");
def modulemeta: error("expression modules are disabled");
def halt: error("expression process control is disabled");
def halt_error: error("expression process control is disabled");
def halt_error($code): error("expression process control is disabled");
def now: error("ambient time access is disabled");
{} as $ENV | (
"""
_POSTLUDE = """
) | if any(..; type == "number" and (isnan or isinfinite))
    then error("non-finite expression output") else . end
"""


class _Program(Protocol):
    def input_value(self, value: Any) -> Iterator[Any]: ...


def validate_expression_data(value: Any, *, depth: int = 0) -> None:
    """Use finite JSON values and an explicit interoperable integer range."""
    invalid = depth > 64
    if value is None or isinstance(value, (str, bool)):
        pass
    elif isinstance(value, int):
        invalid |= abs(value) > MAX_SAFE_INTEGER
    elif isinstance(value, float):
        invalid |= not math.isfinite(value) or (
            value.is_integer() and abs(value) > MAX_SAFE_INTEGER
        )
    elif isinstance(value, Mapping):
        invalid |= any(not isinstance(key, str) for key in value)
        if not invalid:
            for item in value.values():
                validate_expression_data(item, depth=depth + 1)
    elif isinstance(value, list):
        if not invalid:
            for item in value:
                validate_expression_data(item, depth=depth + 1)
    else:
        invalid = True
    if invalid:
        raise ExpressionError(
            "expression data must be finite JSON with safe integers and bounded nesting",
            details={"reason": "invalid_json"},
        )


def expression_string(value: Any) -> str:
    validate_expression_data(value)
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )


def _string_end(text: str, start: int) -> int:
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            if index + 1 < len(text) and text[index + 1] == "(":
                index = _group_end(text, index + 1, "(", ")") + 1
            else:
                index += 2
        elif text[index] == '"':
            return index
        else:
            index += 1
    raise ExpressionError("unterminated expression string")


def _group_end(text: str, start: int, opening: str, closing: str) -> int:
    depth = 1
    index = start + 1
    while index < len(text):
        character = text[index]
        if character == '"':
            index = _string_end(text, index) + 1
            continue
        if character == "#":
            index = _comment_end(text, index)
            continue
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ExpressionError("unterminated runtime expression")


def _comment_end(text: str, start: int) -> int:
    while True:
        newline = text.find("\n", start)
        if newline < 0:
            return len(text)
        line = text[start:newline].rstrip("\r")
        backslashes = len(line) - len(line.rstrip("\\"))
        start = newline + 1
        if backslashes % 2 == 0:
            return start


def _segments(text: str) -> list[tuple[bool, str]]:
    segments: list[tuple[bool, str]] = []
    index = 0
    while index < len(text):
        start = text.find("${", index)
        if start < 0:
            segments.append((False, text[index:]))
            break
        if start > index:
            segments.append((False, text[index:start]))
        end = _group_end(text, start + 1, "{", "}")
        segments.append((True, text[start + 2 : end]))
        index = end + 1
    return segments


def _tokens(text: str) -> Iterator[tuple[str, int]]:
    """Inspect jq code, including interpolation, without treating literals as code."""
    index = 0
    while index < len(text):
        if text[index] == '"':
            end = _string_end(text, index)
            current = index + 1
            while current < end:
                if text[current] == "\\":
                    if current + 1 < end and text[current + 1] == "(":
                        closing = _group_end(text, current + 1, "(", ")")
                        for token, offset in _tokens(text[current + 2 : closing]):
                            yield token, current + 2 + offset
                        current = closing + 1
                    else:
                        current += 2
                else:
                    current += 1
            index = end + 1
        elif text[index] == "#":
            index = _comment_end(text, index)
        else:
            match = re.match(r"\$?[A-Za-z_][A-Za-z0-9_]*", text[index:])
            if match:
                yield match.group(), index
                index += len(match.group())
            else:
                index += 1


def _compile(source: str, variables: Mapping[str, Any]) -> _Program:
    _check_native_configuration()
    for token, position in _tokens(source):
        if token == "$ENV":
            raise ExpressionError("ambient environment access is not enabled")
        if token in {"include", "import"}:
            before = source[:position].rstrip()
            after = source[position + len(token) :].lstrip()
            if not before.endswith(".") and not after.startswith(":"):
                raise ExpressionError("expression modules are not enabled")
    try:
        compiled = jq.compile(_PRELUDE + source + "\n" + _POSTLUDE, args=dict(variables))
    except Exception:
        # Discard native diagnostics and their native state before raising the
        # public error. Retaining a native traceback also delays state cleanup.
        pass
    else:
        return cast(_Program, compiled)
    raise ExpressionError("invalid jq expression")


def _check_native_configuration() -> None:
    # libjq automatically loads ~/.jq even without an import directive. The
    # binding has no API to disable that behavior. Reject the ambient module
    # location before calling native code; never mutate the process environment.
    home = os.environ.get("HOME")
    if home is None and os.name == "nt":
        home = os.environ.get("USERPROFILE")
        if home is None and "HOMEPATH" in os.environ:
            home = os.environ.get("HOMEDRIVE", "") + os.environ["HOMEPATH"]
    if home is None:
        return
    try:
        (Path(home) / ".jq").stat()
    except FileNotFoundError:
        return
    except OSError:
        pass
    raise ExpressionError(
        "ambient jq module configuration is not permitted",
        details={"reason": "unsafe_configuration"},
    )


def _bindings(variables: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in variables.items():
        if key in {"_task_reference", "_task_name"}:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key == "ENV":
            raise ExpressionError("invalid expression variable binding")
        if key == "context" and isinstance(value, Mapping):
            # Until S1-03 separates workflow context from runtime control state,
            # private lifecycle/handle values must never cross the jq boundary.
            value = {
                name: item
                for name, item in value.items()
                if name not in {"_lifecycle", "_invocation_handle"}
            }
        validate_expression_data(value)
        result[key] = copy.deepcopy(value)
    return result


class ExpressionEvaluator:
    """Evaluate one JSON result using jq 1.8.2, packaged by jq.py 1.12.0."""

    def __init__(self, *, mode: ExpressionMode = "strict") -> None:
        self.mode = mode

    def validate(self, expression: Any, *, variables: set[str]) -> None:
        if isinstance(expression, Mapping):
            for value in expression.values():
                self.validate(value, variables=variables)
        elif isinstance(expression, list):
            for value in expression:
                self.validate(value, variables=variables)
        elif isinstance(expression, str) and "${" in expression:
            try:
                for executable, source in _segments(expression):
                    if executable:
                        _compile(source, dict.fromkeys(variables))
            except ExpressionError as exc:
                if self.mode == "strict" or exc.details.get("reason") == "unsafe_configuration":
                    raise

    def evaluate(
        self, expression: Any, data: Any, *, variables: Mapping[str, Any] | None = None
    ) -> Any:
        validate_expression_data(data)
        if isinstance(expression, Mapping):
            return {
                key: self.evaluate(value, data, variables=variables)
                for key, value in expression.items()
            }
        if isinstance(expression, list):
            return [self.evaluate(value, data, variables=variables) for value in expression]
        if not isinstance(expression, str):
            validate_expression_data(expression)
            return copy.deepcopy(expression)
        try:
            if "${" in expression:
                segments = _segments(expression)
                expressions = [source for executable, source in segments if executable]
                literals = [source for executable, source in segments if not executable]
                if len(expressions) == 1 and not "".join(literals).strip():
                    return self._evaluate_atom(expressions[0], data, variables or {})
                return "".join(
                    expression_string(self._evaluate_atom(source, data, variables or {}))
                    if executable
                    else source
                    for executable, source in segments
                )
            if self.mode == "loose":
                return self._evaluate_atom(expression, data, variables or {})
            return expression
        except ExpressionError as exc:
            if self.mode == "loose" and exc.details.get("reason") not in {
                "invalid_json",
                "unsafe_configuration",
            }:
                return expression
            raise

    def condition(
        self, expression: Any, data: Any, *, variables: Mapping[str, Any] | None = None
    ) -> bool:
        result = self.evaluate(expression, data, variables=variables)
        # jq truthiness differs from Python for zero, empty strings and containers.
        return result is not None and result is not False

    def _evaluate_atom(self, expression: str, data: Any, variables: Mapping[str, Any]) -> Any:
        validate_expression_data(data)
        bindings = _bindings(variables)
        program = _compile(expression, bindings)
        missing = object()
        failed = False
        result = second = missing
        results = iter(program.input_value(copy.deepcopy(data)))
        try:
            result = next(results, missing)
            second = next(results, missing)
        except Exception:
            failed = True
        finally:
            # jq.py owns a native state pool. Release the iterator before the
            # program, including when evaluation stops on an invalid stream.
            del results
            del program
        if failed:
            raise ExpressionError("jq expression evaluation failed")
        if result is missing or second is not missing:
            raise ExpressionError("jq expression must produce exactly one JSON value")
        validate_expression_data(result)
        return result
