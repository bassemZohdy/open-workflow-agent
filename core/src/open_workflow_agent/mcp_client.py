"""Pinned, typed MCP calls through deployment-owned common transports."""

from __future__ import annotations

import asyncio
import copy
import json
import math
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import anyio
import mcp_types as types
from mcp.client import ClientSession
from mcp.shared.dispatcher import CallOptions, OnNotify, OnNotifyIntercept, OnRequest
from mcp.shared.exceptions import MCPError
from mcp_types import methods
from pydantic import TypeAdapter, ValidationError

from .errors import OwaError, ToolError, _McpOutcomeUncertain
from .expressions import validate_expression_data

if TYPE_CHECKING:
    from .protocols import AuthenticationProvider, HttpClient

MCP_VERSION = "2026-07-28"
MCP_SDK_VERSION = "2.3.0"
MCP_CALL_METHODS = frozenset(
    {
        "tools/list",
        "tools/call",
        "prompts/list",
        "prompts/get",
        "resources/list",
        "resources/read",
        "resources/templates/list",
    }
)
_RESERVED_META = frozenset(
    {
        "io.modelcontextprotocol/protocolVersion",
        "io.modelcontextprotocol/clientInfo",
        "io.modelcontextprotocol/clientCapabilities",
    }
)


def _verify_sdk() -> None:
    available = {}
    for package in ("mcp", "mcp-types"):
        try:
            installed = distribution_version(package)
        except PackageNotFoundError:
            installed = "missing"
        available[package] = installed
    if any(installed != MCP_SDK_VERSION for installed in available.values()):
        raise ToolError(
            "unsupported MCP SDK installation; requires mcp and mcp-types 2.3.0",
            details={"required": MCP_SDK_VERSION, "available": available},
        )


def _failure(reason: str) -> ToolError:
    return ToolError(
        "MCP call failed",
        details={
            "type": "https://serverlessworkflow.io/spec/1.0.0/errors/runtime",
            "reason": reason,
        },
    )


def _uncertain(reason: str) -> _McpOutcomeUncertain:
    return _McpOutcomeUncertain("MCP outcome remains unresolved", details={"reason": reason})


def validate_supported_configuration(payload: Mapping[str, Any]) -> None:
    """Reject MCP configuration fields with no defined runtime semantics."""
    transport = payload.get("transport")
    if isinstance(transport, Mapping) and "options" in transport:
        raise _failure("invalid_transport")
    client = payload.get("client", {"name": "open-workflow-agent", "version": "0.2.0"})
    if not isinstance(client, Mapping) or set(client) - {"name", "version"}:
        raise _failure("invalid_client")


def _request(payload: Mapping[str, Any], method: str) -> Any:
    try:
        return methods.MONOLITH_REQUESTS[method].model_validate(
            {"method": method, "params": _parameters(payload, method)}, by_name=False
        )
    except (ValidationError, ValueError):
        raise _failure("invalid_parameters") from None


def _response_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in items:
        if name in result:
            raise ValueError("duplicate response key")
        result[name] = value
    return result


def _non_finite_constant(value: str) -> Any:
    raise ValueError("non-finite response")


def _response_json(content: bytes, content_type: str) -> Any:
    """Selected finite SSE profile: one terminal message, no reconnect/replay."""
    try:
        text = content.decode("utf-8")
        if content_type == "text/event-stream":
            frames = text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
            if len(frames) > 65 or frames[-1].strip():
                raise ValueError("incomplete or excessive events")
            messages = []
            for frame in frames[:-1]:
                data = []
                event = "message"
                for line in frame.split("\n"):
                    if not line or line.startswith(":"):
                        continue
                    field, separator, value = line.partition(":")
                    if not separator:
                        raise ValueError("invalid event field")
                    value = value.removeprefix(" ")
                    if field == "event":
                        event = value
                    elif field == "data":
                        data.append(value)
                    elif field != "id" or len(value) > 256:
                        raise ValueError("unsupported event field")
                if not data:
                    continue
                message = "\n".join(data)
                if event != "message" or len(message.encode("utf-8")) > 1_048_576:
                    raise ValueError("unsupported or oversized event")
                messages.append(message)
            if len(messages) != 1:
                raise ValueError("one correlated result required")
            text = messages[0]
        elif content_type != "application/json":
            raise ValueError("unsupported response type")
        return json.loads(
            text,
            object_pairs_hook=_response_pairs,
            parse_constant=_non_finite_constant,
        )
    except (ValueError, UnicodeError):
        raise _uncertain("invalid_response") from None


def _parameters(payload: Mapping[str, Any], method: str) -> dict[str, Any]:
    value = payload.get("parameters")
    if value is None:
        # Preserve the configured agent tool shorthand; OWS callers use parameters.
        value = (
            {
                "name": payload.get("name", payload.get("tool")),
                "arguments": payload.get("arguments", payload.get("input", {})),
            }
            if method == "tools/call"
            else {}
        )
    if not isinstance(value, dict):
        raise _failure("invalid_parameters")
    validate_expression_data(value)
    result = copy.deepcopy(value)
    meta = result.get("_meta", {})
    if not isinstance(meta, dict) or _RESERVED_META.intersection(meta):
        raise _failure("reserved_metadata")
    if "inputResponses" in result or "requestState" in result:
        raise _failure("unsupported_interaction")
    return result


class _CommonHttpDispatcher:
    """One operation, one bounded stateless HTTP request, no SDK retries."""

    def __init__(
        self,
        http: HttpClient,
        endpoint: str,
        headers: dict[str, str],
        *,
        operation_id: str | None,
        request_id: str | int,
        timeout: float,
        authentication: AuthenticationProvider | None,
    ) -> None:
        self.http, self.endpoint, self.headers = http, endpoint, headers
        self.operation_id, self.request_id, self.timeout = operation_id, request_id, timeout
        self.authentication = authentication
        self._dispatched = False
        self._attempted = False
        self._correlated_error = False
        self._closed = anyio.Event()

    def _mark_dispatched(self) -> None:
        self._dispatched = True

    async def run(
        self,
        on_request: OnRequest,
        on_notify: OnNotify,
        on_notify_intercept: OnNotifyIntercept | None = None,
        *,
        task_status: anyio.abc.TaskStatus[None] = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        task_status.started()
        await self._closed.wait()

    async def notify(
        self,
        method: str,
        params: Mapping[str, Any] | None,
        opts: CallOptions | None = None,
    ) -> None:
        raise _failure("unsupported_notification")

    async def send_raw_request(
        self,
        method: str,
        params: Mapping[str, Any] | None,
        opts: CallOptions | None = None,
    ) -> dict[str, Any]:
        from .protocols import _merge_authoritative_headers

        if self._attempted or method not in MCP_CALL_METHODS:
            raise _failure("unexpected_dispatch")
        methods.validate_client_request(method, MCP_VERSION, params)
        options = opts or {}
        headers = _merge_authoritative_headers(self.headers, options.get("headers", {}))
        headers = _merge_authoritative_headers(
            headers,
            {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
        )
        body = {"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}
        if len(json.dumps(body, allow_nan=False).encode("utf-8")) > 1_048_576:
            raise _failure("request_limit")
        self._attempted = True
        try:
            response = await self.http.request(
                "POST",
                self.endpoint,
                headers=headers,
                json=body,
                operation_id=self.operation_id,
                request_timeout=self.timeout,
                follow_redirects=False,
                _return_response=True,
                _authentication=self.authentication,
                _on_dispatch=self._mark_dispatched,
            )
        except ToolError:
            if self._dispatched:
                raise _uncertain("transport_failure") from None
            raise
        try:
            raw = _response_json(
                response.content,
                response.headers.get("content-type", "").split(";", 1)[0].strip().lower(),
            )
            validate_expression_data(raw)
        except (OwaError, ValueError, UnicodeError):
            raise _uncertain("invalid_response") from None
        if (
            isinstance(raw, dict)
            and raw.get("jsonrpc") == "2.0"
            and isinstance(raw.get("method"), str)
            and "id" not in raw
        ):
            # A server notification is a separate interaction, not the result
            # of this business request. Do not let the SDK turn it into a
            # fabricated completion or attempt the request again.
            raise _uncertain("unsupported_interaction")
        if (
            not isinstance(raw, dict)
            or raw.get("jsonrpc") != "2.0"
            or type(raw.get("id")) is not type(self.request_id)
            or raw.get("id") != self.request_id
            or ("result" in raw) == ("error" in raw)
        ):
            raise _uncertain("invalid_response")
        if "error" in raw:
            error = types.ErrorData.model_validate(raw["error"], by_name=False)
            self._correlated_error = True
            raise MCPError(error.code, "MCP server returned an error")
        result = raw["result"]
        if not isinstance(result, dict):
            raise _uncertain("invalid_result")
        return result


async def _typed_call(session: ClientSession, payload: Mapping[str, Any], method: str) -> Any:
    request = _request(payload, method)
    result: types.Result = await session.send_request(
        request,
        TypeAdapter[types.Result](methods.MONOLITH_RESULTS[method]),  # type: ignore[arg-type]
    )
    if isinstance(result, types.InputRequiredResult):
        raise _uncertain("unsupported_interaction")
    if isinstance(result, types.CallToolResult) and result.is_error:
        raise _failure("tool_error")
    return result.model_dump(mode="json", by_alias=True, exclude_unset=True)


async def _session_call(
    session: ClientSession,
    adopted: types.DiscoverResult,
    payload: Mapping[str, Any],
    method: str,
) -> Any:
    # Keep ordinary common/protocol faults outside AnyIO task-group teardown,
    # which otherwise wraps an operation error into a framework ExceptionGroup.
    failure: OwaError | MCPError | ValidationError | None = None
    async with session:
        session.adopt(adopted)
        try:
            result = await _typed_call(session, payload, method)
        except (OwaError, MCPError, ValidationError) as error:
            failure = error
    if failure is not None:
        raise failure
    return result


async def call_mcp(
    http: HttpClient,
    payload: Mapping[str, Any],
    *,
    timeout: float,
    operation_id: str | None = None,
    authentication: AuthenticationProvider | None = None,
    sandbox: Any = None,
) -> Any:
    """Use the pinned SDK type layer without implicit discovery or replay."""
    _verify_sdk()
    validate_supported_configuration(payload)
    if not math.isfinite(timeout) or timeout <= 0:
        raise _failure("invalid_timeout")
    version = payload.get("protocolVersion", MCP_VERSION)
    if version != MCP_VERSION:
        raise ToolError(
            f"unsupported MCP protocol version: {version}; supported version is {MCP_VERSION}"
        )
    method = payload.get("method", "tools/call")
    if not isinstance(method, str) or method not in MCP_CALL_METHODS:
        raise ToolError(f"unsupported MCP method: {method}")
    transport = payload.get("transport", {})
    if not isinstance(transport, dict):
        raise ToolError("mcp call requires a transport object")
    if set(transport) - {"http", "stdio"}:
        raise _failure("invalid_transport")
    if "http" in transport and "stdio" in transport:
        raise _failure("invalid_transport")
    if "stdio" in transport:
        if authentication is not None:
            raise ToolError("mcp stdio cannot use an HTTP authentication profile")
        if sandbox is None:
            raise ToolError("mcp stdio transport is not enabled")
    else:
        from .protocols import _endpoint_value

        http_value = transport.get("http", {})
        if not isinstance(http_value, dict):
            raise ToolError("mcp HTTP transport must be an object")
        if not _endpoint_value(payload.get("endpoint") or http_value.get("endpoint")):
            raise ToolError("mcp call requires transport.http.endpoint")
    # Validate before opening either transport or resolving credentials.
    _request(payload, method)
    client = payload.get("client", {"name": "open-workflow-agent", "version": "0.2.0"})
    try:
        info = types.Implementation.model_validate(client, by_name=False)
    except ValidationError:
        raise _failure("invalid_client") from None
    adopted = types.DiscoverResult(
        supported_versions=[MCP_VERSION],
        capabilities=types.ServerCapabilities(),
        result_type="complete",
        ttl_ms=0,
        cache_scope="public",
    )
    dispatched = False
    correlated_error = False
    dispatcher: _CommonHttpDispatcher | None = None

    def observe_dispatch() -> None:
        nonlocal dispatched
        dispatched = True

    def observe_correlated_error() -> None:
        nonlocal correlated_error
        correlated_error = True

    try:
        async with asyncio.timeout(timeout):
            if "stdio" in transport:
                if sandbox is None:
                    raise ToolError("mcp stdio transport is not enabled")
                from .mcp_stdio import sandbox_stdio_transport

                async with sandbox_stdio_transport(
                    sandbox,
                    transport,
                    timeout_seconds=timeout,
                    execution_id=operation_id,
                    on_dispatch=observe_dispatch,
                    on_correlated_error=observe_correlated_error,
                ) as (read_stream, write_stream):
                    session = ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timeout,
                        client_info=info,
                    )
                    return await _session_call(session, adopted, payload, method)
            from .protocols import _endpoint_value

            http_transport = transport.get("http", {})
            if not isinstance(http_transport, dict):
                raise ToolError("mcp HTTP transport must be an object")
            endpoint = _endpoint_value(payload.get("endpoint") or http_transport.get("endpoint"))
            if not endpoint:
                raise ToolError("mcp call requires transport.http.endpoint")
            http._check_endpoint(str(endpoint))
            request_id = payload.get("id", operation_id or str(uuid4()))
            if type(request_id) not in (str, int):
                raise _failure("invalid_request_id")
            headers = http_transport.get("headers", {})
            if not isinstance(headers, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in headers.items()
            ):
                raise _failure("invalid_headers")
            dispatcher = _CommonHttpDispatcher(
                http,
                str(endpoint),
                headers,
                operation_id=operation_id,
                request_id=request_id,
                timeout=timeout,
                authentication=authentication,
            )
            session = ClientSession(
                dispatcher=dispatcher, read_timeout_seconds=timeout, client_info=info
            )
            return await _session_call(session, adopted, payload, method)
    except MCPError:
        if correlated_error or (dispatcher is not None and dispatcher._correlated_error):
            raise _failure("protocol_error") from None
        if dispatched or (dispatcher is not None and dispatcher._dispatched):
            raise _uncertain("transport_failure") from None
        raise _failure("transport_failure") from None
    except ValidationError:
        raise _uncertain("invalid_protocol_outcome") from None
    except TimeoutError:
        if dispatched or (dispatcher is not None and dispatcher._dispatched):
            raise _uncertain("timeout") from None
        raise _failure("timeout") from None
