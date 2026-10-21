"""Bounded ephemeral literal credential projection guard.

This is a direct-echo guard, not general taint tracking or encoded-secret
recognition. Values are captured from the actual prepared call/environment,
never re-resolved after execution, persisted, or inherited as ambient state.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .errors import OwaError, ToolError

_MAX_VALUES = 256
_MAX_VALUE_BYTES = 65_536
_MAX_TOTAL_BYTES = 1_048_576
_REPLACEMENT = "[REDACTED]"
_ACTIVE: ContextVar[tuple[LiteralCredentialGuard, ...]] = ContextVar(
    "owa_literal_credentials", default=()
)


@dataclass(slots=True, repr=False)
class LiteralCredentialGuard:
    _values: set[str] = field(default_factory=set)
    _bytes: int = 0

    def __repr__(self) -> str:
        return "LiteralCredentialGuard(<private values redacted>)"

    def capture(self, values: Iterable[str]) -> None:
        for value in values:
            if not isinstance(value, str):
                raise ToolError("credential projection binding is invalid")
            if not value or value in self._values:
                continue
            try:
                size = len(value.encode("utf-8", errors="strict"))
            except UnicodeError:
                raise ToolError("credential projection binding is invalid") from None
            if (
                size > _MAX_VALUE_BYTES
                or len(self._values) >= _MAX_VALUES
                or self._bytes + size > _MAX_TOTAL_BYTES
            ):
                raise ToolError("credential projection binding exceeds finite limits")
            self._values.add(value)
            self._bytes += size

    def text(self, value: str) -> str:
        replacement = "" if any(secret in _REPLACEMENT for secret in self._values) else _REPLACEMENT
        for secret in sorted(self._values, key=lambda item: (-len(item), item)):
            value = value.replace(secret, replacement)
        return value

    def raw(self, value: bytes) -> bytes:
        replacement = (
            b""
            if any(secret in _REPLACEMENT for secret in self._values)
            else _REPLACEMENT.encode("ascii")
        )
        for secret in sorted(self._values, key=lambda item: (-len(item), item)):
            value = value.replace(secret.encode("utf-8"), replacement)
        return value

    def contains_literal(self, value: str | bytes) -> bool:
        if isinstance(value, bytes):
            return any(secret.encode("utf-8") in value for secret in self._values)
        return any(secret in value for secret in self._values)

    def project(self, value: Any) -> Any:
        if not self._values:
            return value
        visited = 0

        def selected(item: Any, depth: int) -> Any:
            nonlocal visited
            visited += 1
            if visited > 100_000 or depth > 64:
                raise ToolError("credential projection exceeds finite limits")
            if isinstance(item, str):
                return self.text(item)
            if isinstance(item, list):
                return [selected(child, depth + 1) for child in item]
            if isinstance(item, tuple):
                return tuple(selected(child, depth + 1) for child in item)
            if isinstance(item, dict):
                result: dict[Any, Any] = {}
                for key, child in item.items():
                    name = self.text(key) if isinstance(key, str) else key
                    if name in result:
                        raise ToolError("credential projection contains conflicting field names")
                    result[name] = selected(child, depth + 1)
                return result
            return item

        return selected(value, 0)

    def error(self, error: OwaError) -> None:
        error.message = self.text(error.message)
        error.details = self.project(error.details)
        error.args = (error.message,)


@contextmanager
def credential_scope() -> Iterator[LiteralCredentialGuard]:
    guard = LiteralCredentialGuard()
    token = _ACTIVE.set((*_ACTIVE.get(), guard))
    try:
        yield guard
    finally:
        _ACTIVE.reset(token)
        guard._values.clear()
        guard._bytes = 0


def capture_credentials(*values: str) -> None:
    for guard in _ACTIVE.get():
        guard.capture(values)


def capture_authentication_headers(headers: Mapping[str, str]) -> None:
    values: list[str] = []
    for name, value in headers.items():
        values.append(value)
        if name.lower() in {"authorization", "proxy-authorization"}:
            scheme, separator, credential = value.partition(" ")
            if scheme.lower() == "bearer" and separator:
                # RFC bearer credentials permit one or more spaces after the
                # scheme. Capture the bare token as well as the full header so
                # an echoed token is covered when the separator repeats.
                credential = credential.lstrip(" ")
                if credential:
                    values.append(credential)
    capture_credentials(*values)


def active_projection_guard() -> LiteralCredentialGuard | None:
    guards = _ACTIVE.get()
    return guards[-1] if guards else None
