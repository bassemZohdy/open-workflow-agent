"""Bounded inbound A2A exposure behind a deployment-selected transport.

Disabled by default. When enabled, the runtime exposes an Agent Card and a
synchronous `message/send` endpoint so external A2A clients can drive the
configured workflow. Two transport implementations are selectable through
configuration: `jsonrpc` (JSON-RPC 2.0 over HTTP, the most widely deployed
A2A transport, the default) and `http_json` (A2A HTTP+JSON, messages posted
as plain A2A objects). Streaming (`message/stream`), push notifications,
and persistent task objects are intentionally out of this bounded profile.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import A2AConfig

_MAX_TEXT_PARTS = 64


# Well-known transport labels from the A2A agent-card specification.
_TRANSPORT_LABELS = {"jsonrpc": "JSONRPC", "http_json": "HTTP+JSON"}


def a2a_capabilities(config: A2AConfig) -> dict[str, Any]:
    """Sanitized `features.a2a` capability block."""

    return {
        "enabled": config.enabled,
        "transport": config.transport,
        "card": f"{config.path}/agent.json" if config.enabled else None,
        "streaming": False,
        "pushNotifications": False,
        "auth": "bearer" if config.enabled and config.auth_token else None,
    }


def build_agent_card(config: A2AConfig, *, url: str, workflow_name: str) -> dict[str, Any]:
    """Build the bounded Agent Card for the configured workflow."""

    transport_label = _TRANSPORT_LABELS[config.transport]
    return {
        "name": config.agent_name,
        "description": config.agent_description,
        "url": url,
        "version": config.agent_version,
        "protocolVersion": "0.3.0",
        "preferredTransport": transport_label,
        "additionalInterfaces": [
            {"transport": label, "path": config.path if label == transport_label else None}
            for label in _TRANSPORT_LABELS.values()
        ],
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "workflow",
                "name": workflow_name,
                "description": (
                    "Executes the deployment-configured workflow with the message text as input."
                ),
                "tags": ["workflow"],
            }
        ],
    }


def extract_message_text(parts: Any) -> str:
    """Join A2A text parts into the single input text of the bounded profile."""

    if not isinstance(parts, list) or not parts:
        raise ValueError("message.parts must be a non-empty array")
    if len(parts) > _MAX_TEXT_PARTS:
        raise ValueError(f"message.parts exceeds {_MAX_TEXT_PARTS} parts")
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("message.parts entries must be objects")
        if part.get("kind") not in {None, "text"}:
            raise ValueError("only text parts are supported in the bounded A2A profile")
        text = part.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("text parts require a non-empty text string")
        chunks.append(text)
    return "\n".join(chunks)


def extract_output_text(output: Any) -> str:
    """Bounded extraction of an agent-facing text reply from workflow output."""

    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        for key in ("text", "response", "message", "output"):
            value = output.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                inner = extract_output_text(value)
                if inner:
                    return inner
                continue
    if output is None:
        return ""
    return json.dumps(output, ensure_ascii=False)


class JsonRpcError(Exception):
    """JSON-RPC 2.0 error with a sanitized, bounded code."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    APPLICATION_ERROR = -32000

    def __init__(
        self,
        code: int,
        message: str,
        *,
        details: Any = None,
        http_status: int = 200,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.http_status = http_status


def _jsonrpc_response(request_id: Any, result: Any) -> JSONResponse:
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": result})


def _jsonrpc_error(request_id: Any, error: JsonRpcError) -> JSONResponse:
    content: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": error.code, "message": error.message},
    }
    if error.details is not None:
        content["error"]["data"] = error.details
    return JSONResponse(status_code=error.http_status, content=content)


def _authorize(config: A2AConfig, request: Request) -> bool:
    if not config.auth_token:
        return True
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization.removeprefix("Bearer ").strip(), config.auth_token)


def mount_a2a(
    app: FastAPI,
    config: A2AConfig,
    *,
    invoke_message: Callable[[str], Awaitable[tuple[str, str]]],
) -> None:
    """Mount the selected A2A transport implementation on the application.

    `invoke_message(text) -> tuple[str, str]` executes the configured workflow
    with the message text and returns `(reply_text, error)` where `error` is a
    sanitized common-contract error code or an empty string.
    """

    if not config.enabled:
        return

    def authorized(request: Request) -> JSONResponse | None:
        if not _authorize(config, request):
            if config.transport == "jsonrpc":
                return _jsonrpc_error(
                    None,
                    JsonRpcError(
                        JsonRpcError.APPLICATION_ERROR,
                        "unauthorized",
                        http_status=401,
                    ),
                )
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "unauthorized", "message": "a2a authorization failed"}},
            )
        return None

    @app.get(f"{config.path}/agent.json", response_model=None)
    @app.get("/.well-known/agent.json", response_model=None)
    async def agent_card(request: Request) -> JSONResponse | dict[str, Any]:
        if unauthorized := authorized(request):
            return unauthorized
        base = config.public_base_url or str(request.base_url).rstrip("/")
        return build_agent_card(
            config,
            url=f"{base}{config.path}",
            workflow_name=str(getattr(app.state, "workflow_name", config.agent_name)),
        )

    if config.transport == "jsonrpc":

        @app.post(config.path)
        async def jsonrpc_entry(request: Request) -> JSONResponse:
            if unauthorized := authorized(request):
                return unauthorized
            try:
                envelope = await request.json()
            except ValueError as exc:
                raise JsonRpcError(JsonRpcError.PARSE_ERROR, "invalid JSON payload") from exc
            if not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0":
                raise JsonRpcError(JsonRpcError.INVALID_REQUEST, "not a JSON-RPC 2.0 request")
            request_id = envelope.get("id")
            method = envelope.get("method")
            if method != "message/send":
                raise JsonRpcError(
                    JsonRpcError.METHOD_NOT_FOUND,
                    "only message/send is supported in the bounded A2A profile",
                )
            params = envelope.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("message"), dict):
                raise JsonRpcError(
                    JsonRpcError.INVALID_PARAMS, "message/send requires params.message"
                )
            message = params["message"]
            try:
                text = extract_message_text(message.get("parts"))
            except ValueError as exc:
                raise JsonRpcError(JsonRpcError.INVALID_PARAMS, str(exc)) from exc
            if len(text) > config.max_message_chars:
                raise JsonRpcError(
                    JsonRpcError.INVALID_PARAMS,
                    f"message text exceeds {config.max_message_chars} characters",
                )
            reply, error_code = await invoke_message(text)
            if error_code:
                raise JsonRpcError(
                    JsonRpcError.APPLICATION_ERROR,
                    "workflow execution failed",
                    details={"code": error_code},
                )
            result = {
                "kind": "message",
                "messageId": f"a2a-{message.get('messageId', 'reply')}-reply"
                if isinstance(message.get("messageId"), str)
                else "a2a-reply",
                "role": "agent",
                "parts": [{"kind": "text", "text": reply}],
            }
            return _jsonrpc_response(request_id, result)

        @app.exception_handler(JsonRpcError)
        async def jsonrpc_error(_request: Request, error: JsonRpcError) -> JSONResponse:
            return _jsonrpc_error(None, error)

    else:

        @app.post(config.path, response_model=None)
        async def http_json_entry(request: Request) -> JSONResponse | dict[str, Any]:
            if unauthorized := authorized(request):
                return unauthorized
            try:
                message = await request.json()
            except ValueError:
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": {"code": "invalid_request", "message": "invalid JSON payload"}
                    },
                )
            if not isinstance(message, dict):
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "code": "invalid_request",
                            "message": "body must be an A2A message object",
                        }
                    },
                )
            try:
                text = extract_message_text(message.get("parts"))
            except ValueError as exc:
                return JSONResponse(
                    status_code=422,
                    content={"error": {"code": "invalid_request", "message": str(exc)}},
                )
            if len(text) > config.max_message_chars:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "message_too_large",
                            "message": (
                                f"message text exceeds {config.max_message_chars} characters"
                            ),
                        }
                    },
                )
            reply, error_code = await invoke_message(text)
            if error_code:
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "code": error_code,
                            "message": "workflow execution failed",
                        }
                    },
                )
            return {
                "kind": "message",
                "messageId": message.get("messageId", "a2a-reply"),
                "role": "agent",
                "parts": [{"kind": "text", "text": reply}],
            }


__all__ = [
    "A2AConfig",
    "JsonRpcError",
    "a2a_capabilities",
    "build_agent_card",
    "extract_message_text",
    "extract_output_text",
    "mount_a2a",
]
