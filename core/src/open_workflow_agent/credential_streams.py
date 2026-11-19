"""Private bounded text projection for future common model streaming.

This helper does not enable a provider or advertise a streaming capability.
It must be created after credential preparation, inside the active credential
scopes. Closing any enclosing scope discards its retained raw text and bindings.
"""

from __future__ import annotations

from typing import NoReturn

from .credential_guard import _ACTIVE, _REPLACEMENT, LiteralCredentialGuard
from .errors import ToolError


def _replacement(values: tuple[str, ...]) -> str:
    # Both boundary characters exclude matches assembled across a replacement.
    # A credential contained in the marker itself must also remain private.
    if all("[" not in value and "]" not in value and value not in _REPLACEMENT for value in values):
        return _REPLACEMENT
    used = set("".join(values))
    for character in ("█", "■", "●", "�"):
        if character not in used:
            return character
    # Captured literals contain at most 1 MiB. They cannot contain every scalar
    # in these supplementary planes; skip the reserved noncharacter endings.
    for point in range(0x10000, 0x10FFFE):
        if point & 0xFFFF < 0xFFFE and chr(point) not in used:
            return chr(point)
    raise ToolError("credential stream replacement is unavailable")


class CredentialTextStream:
    """Project fragments without publishing an incomplete credential prefix.

    The raw look-behind retains at most longest_literal - 1 characters. A mask
    tracks the union of overlapping matches, including matches crossing feeds.
    Resource/projection failures close the helper before raising a safe error.
    The caller owns observation failure handling; this object dispatches no work.
    """

    __slots__ = (
        "_guards",
        "_values",
        "_hold",
        "_replacement",
        "_pending",
        "_mask",
        "_redacting",
        "_closed",
        "_raw_bytes",
        "_projected_bytes",
        "_chunks",
        "_max_chunk_bytes",
        "_max_total_bytes",
        "_max_chunks",
    )

    def __init__(
        self,
        *,
        max_chunk_bytes: int = 65_536,
        max_total_bytes: int = 1_048_576,
        max_chunks: int = 4096,
    ) -> None:
        for value, maximum in (
            (max_chunk_bytes, 262_144),
            (max_total_bytes, 16_777_216),
            (max_chunks, 65_536),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ToolError("credential stream limits are invalid")
        if max_chunk_bytes > max_total_bytes:
            raise ToolError("credential stream chunk exceeds its total budget")
        guards = _ACTIVE.get()
        if not guards or any(guard._closed for guard in guards):
            raise ToolError("credential stream requires an active credential scope")
        if any(len(guard._stream_closers) >= 32 for guard in guards):
            raise ToolError("credential stream scope capacity exceeded")
        merged = LiteralCredentialGuard()
        try:
            for guard in guards:
                merged.capture(guard._values)
            values = tuple(sorted(merged._values, key=lambda value: (-len(value), value)))
        finally:
            merged._close()
        self._guards = tuple((guard, guard._revision) for guard in guards)
        self._values = values
        self._hold = max((len(value) - 1 for value in values), default=0)
        self._replacement = _replacement(values)
        self._pending = ""
        self._mask = bytearray()
        self._redacting = False
        self._closed = False
        self._raw_bytes = self._projected_bytes = self._chunks = 0
        self._max_chunk_bytes = max_chunk_bytes
        self._max_total_bytes = max_total_bytes
        self._max_chunks = max_chunks
        for guard in guards:
            guard._stream_closers.append(self.close)

    def __repr__(self) -> str:
        return "CredentialTextStream(<redacted>)"

    def _fail(self, message: str) -> NoReturn:
        self.close()
        raise ToolError(message) from None

    def _check(self) -> None:
        if self._closed:
            raise ToolError("credential stream is closed")
        if any(guard._closed or guard._revision != revision for guard, revision in self._guards):
            self._fail("credential stream binding changed")

    def feed(self, text: str) -> str:
        self._check()
        if type(text) is not str or len(text) > self._max_chunk_bytes:
            self._fail("credential stream fragment is invalid or oversized")
        try:
            size = len(text.encode("utf-8", errors="strict"))
        except UnicodeError:
            self._fail("credential stream fragment is invalid")
        if (
            size > self._max_chunk_bytes
            or self._raw_bytes + size > self._max_total_bytes
            or self._chunks >= self._max_chunks
        ):
            self._fail("credential stream input limit exceeded")
        self._raw_bytes += size
        self._chunks += 1
        if not text:
            return ""
        previous = len(self._pending)
        self._pending += text
        self._mask.extend(b"\0" * len(text))
        for value in self._values:
            position = max(0, previous - len(value) + 1)
            while (position := self._pending.find(value, position)) >= 0:
                self._mask[position : position + len(value)] = b"\1" * len(value)
                position += 1
            if 0 not in self._mask:
                break
        return self._drain(max(0, len(self._pending) - self._hold))

    def _drain(self, count: int) -> str:
        parts = []
        for index in range(count):
            if self._mask[index]:
                if not self._redacting:
                    parts.append(self._replacement)
                self._redacting = True
            else:
                parts.append(self._pending[index])
                self._redacting = False
        result = "".join(parts)
        projected = len(result.encode("utf-8"))
        if self._projected_bytes + projected > self._max_total_bytes:
            self._fail("credential stream projection limit exceeded")
        self._projected_bytes += projected
        self._pending = self._pending[count:]
        del self._mask[:count]
        return result

    def finish(self) -> str:
        self._check()
        try:
            return self._drain(len(self._pending))
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for guard, _ in self._guards:
            if self.close in guard._stream_closers:
                guard._stream_closers.remove(self.close)
        self._guards = ()
        self._values = ()
        self._pending = ""
        self._mask.clear()
        self._replacement = ""
