"""Official MCP message streams over common sandbox-owned process bytes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from .errors import SandboxProcessError, ToolError
from .sandbox import SandboxManager, SandboxStdioRequest


def _strict_frame(value: bytes) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    def reject_constant(_value: str) -> Any:
        raise ValueError("non-finite JSON value")

    try:
        result = json.loads(
            value.decode("utf-8", errors="strict"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
        if not isinstance(result, dict) or result.get("jsonrpc") != "2.0":
            raise ValueError("invalid JSON-RPC frame")
        return result
    except (UnicodeError, ValueError, RecursionError):
        raise ToolError("MCP stdio response frame is invalid") from None


@asynccontextmanager
async def sandbox_stdio_transport(
    manager: SandboxManager,
    transport: dict[str, Any],
    *,
    timeout_seconds: float,
    execution_id: str | None = None,
    on_dispatch: Callable[[], None] | None = None,
    on_correlated_error: Callable[[], None] | None = None,
) -> AsyncIterator[tuple[MemoryObjectReceiveStream[Any], MemoryObjectSendStream[Any]]]:
    try:
        from mcp.shared.message import SessionMessage
        from mcp_types import jsonrpc_message_adapter
    except ImportError:
        raise ToolError("the pinned MCP dependency is unavailable") from None
    value = transport.get("stdio")
    if not isinstance(value, dict) or set(transport) != {"stdio"}:
        raise ToolError("MCP stdio transport configuration is invalid")
    request = SandboxStdioRequest(
        execution_id or "mcp-stdio:" + uuid4().hex,
        value.get("command", ""),
        tuple(value.get("arguments", [])),
        tuple(value["environment"].items()) if "environment" in value else None,
        timeout_seconds,
    )
    async with manager.open_stdio(request) as session:
        backend = manager.backend
        capacity = getattr(getattr(backend, "config", None), "mcp_stdio", None)
        size = capacity.max_pending_frames if capacity is not None else 1
        incoming_send, incoming_read = anyio.create_memory_object_stream[Any](size)
        outgoing_write, outgoing_read = anyio.create_memory_object_stream[Any](size)
        pending: set[tuple[type[Any], str | int]] = set()

        async def read() -> None:
            try:
                while True:
                    frame = _strict_frame(await session.receive_frame())
                    identifier = frame.get("id")
                    if (
                        type(identifier) not in (str, int)
                        or (type(identifier), identifier) not in pending
                        or ("result" in frame) == ("error" in frame)
                        or "method" in frame
                    ):
                        raise ToolError("MCP stdio response correlation is invalid")
                    try:
                        message = jsonrpc_message_adapter.validate_python(frame, by_name=False)
                    except Exception:
                        raise ToolError("MCP stdio response message is invalid") from None
                    pending.remove((type(identifier), cast(str | int, identifier)))
                    if "error" in frame and on_correlated_error is not None:
                        on_correlated_error()
                    await incoming_send.send(SessionMessage(message))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await incoming_send.send(error)
            finally:
                await incoming_send.aclose()

        async def write() -> None:
            try:
                async for message in outgoing_read:
                    data = message.message.model_dump(mode="json", by_alias=True, exclude_none=True)
                    encoded = json.dumps(
                        data, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                    ).encode("utf-8")
                    _strict_frame(encoded)
                    if "id" in data:
                        identifier = data["id"]
                        if (
                            type(identifier) not in (str, int)
                            or (type(identifier), identifier) in pending
                            or len(pending) >= size
                        ):
                            raise ToolError("MCP stdio request correlation is invalid")
                        pending.add((type(identifier), identifier))
                    await session.send_frame(encoded, on_dispatch=on_dispatch)
            except asyncio.CancelledError:
                raise
            except Exception:
                await incoming_send.send(SandboxProcessError("MCP stdio dispatch failed"))

        readers = (asyncio.create_task(read()), asyncio.create_task(write()))
        try:
            yield incoming_read, outgoing_write
        finally:
            with anyio.CancelScope(shield=True):
                for task in readers:
                    task.cancel()
                await session.aclose()
                await asyncio.gather(*readers, return_exceptions=True)
                await incoming_read.aclose()
                await incoming_send.aclose()
                await outgoing_write.aclose()
                await outgoing_read.aclose()
