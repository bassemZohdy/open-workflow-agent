"""Bounded inbound A2A 1.0 profile behind a deployment-selected binding.

The runtime targets A2A protocol release 1.0.1 and advertises protocol version
1.0 on each AgentInterface. Disabled by default. When enabled, the runtime
exposes the standard Agent Card discovery URI and a synchronous SendMessage
operation so external A2A clients can drive the configured workflow.

Two bindings are selectable through deployment configuration:

- ``jsonrpc``: JSON-RPC 2.0 over HTTP using A2A v1 PascalCase methods.
- ``http_json``: A2A HTTP+JSON using the standard REST endpoint/media type.

Task persistence, streaming, resubscription, push notifications, and extended
Agent Cards are intentionally outside this bounded slice.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import A2AConfig

A2A_SPEC_RELEASE = "1.0.1"
A2A_PROTOCOL_VERSION = "1.0"
A2A_AGENT_CARD_PATH = "/.well-known/agent-card.json"
A2A_HTTP_JSON_MEDIA_TYPE = "application/a2a+json"

_MAX_TEXT_PARTS = 64
_TRANSPORT_LABELS = {"jsonrpc": "JSONRPC", "http_json": "HTTP+JSON"}


def a2a_capabilities(config: A2AConfig) -> dict[str, Any]:
    """Return the sanitized ``features.a2a`` capability block."""

    return {
        "enabled": config.enabled,
        "specRelease": A2A_SPEC_RELEASE if config.enabled else None,
        "protocolVersion": A2A_PROTOCOL_VERSION if config.enabled else None,
        "transport": config.transport,
        "card": A2A_AGENT_CARD_PATH if config.enabled else None,
        "streaming": False,
        "pushNotifications": False,
        "tasks": False,
        "auth": "bearer" if config.enabled and config.auth_token else None,
    }


def build_agent_card(config: A2AConfig, *, url: str, workflow_name: str) -> dict[str, Any]:
    """Build the bounded A2A v1 Agent Card for the configured workflow."""

    return {
        "name": config.agent_name,
        "description": config.agent_description,
        "supportedInterfaces": [
            {
                "url": url,
                "protocolBinding": _TRANSPORT_LABELS[config.transport],
                "protocolVersion": A2A_PROTOCOL_VERSION,
            }
        ],
        "version": config.agent_version,
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
    """Join A2A v1 text Parts into the bounded profile's workflow input."""

    if not isinstance(parts, list) or not parts:
        raise ValueError("message.parts must be a non-empty array")
    if len(parts) > _MAX_TEXT_PARTS:
        raise ValueError(f"message.parts exceeds {_MAX_TEXT_PARTS} parts")

    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("message.parts entries must be objects")
        if "kind" in part:
            raise ValueError("legacy A2A part.kind is not supported by the v1 profile")
        text = part.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("only A2A v1 text parts with a non-empty text field are supported")
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


def _http_json_response(content: dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=content,
        media_type=A2A_HTTP_JSON_MEDIA_TYPE,
    )


def _authorize(config: A2AConfig, request: Request) -> bool:
    if not config.auth_token:
        return True
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization.removeprefix("Bearer ").strip(), config.auth_token)


def _reply_message(source_message: dict[str, Any], reply: str) -> dict[str, Any]:
    source_id = source_message.get("messageId")
    return {
        "messageId": f"a2a-{source_id}-reply" if isinstance(source_id, str) else "a2a-reply",
        "role": "ROLE_AGENT",
        "parts": [{"text": reply}],
    }


def mount_a2a(
    app: FastAPI,
    config: A2AConfig,
    *,
    invoke_message: Callable[[str], Awaitable[tuple[str, str]]],
) -> None:
    """Mount the selected bounded A2A v1 binding on the application.

    ``invoke_message(text) -> tuple[str, str]`` executes the configured
    workflow and returns ``(reply_text, error_code)``. Error codes are the
    sanitized common-runtime contract, not engine exceptions.
    """

    if not config.enabled:
        return

    def authorized(request: Request) -> JSONResponse | None:
        if _authorize(config, request):
            return None
        if config.transport == "jsonrpc":
            return _jsonrpc_error(
                None,
                JsonRpcError(
                    JsonRpcError.APPLICATION_ERROR,
                    "unauthorized",
                    http_status=401,
                ),
            )
        return _http_json_response(
            {"error": {"code": "unauthorized", "message": "a2a authorization failed"}},
            status_code=401,
        )

    @app.get(A2A_AGENT_CARD_PATH, response_model=None)
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
            if method != "SendMessage":
                raise JsonRpcError(
                    JsonRpcError.METHOD_NOT_FOUND,
                    "only SendMessage is supported in the bounded A2A profile",
                )
            params = envelope.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("message"), dict):
                raise JsonRpcError(
                    JsonRpcError.INVALID_PARAMS, "SendMessage requires params.message"
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
            return _jsonrpc_response(request_id, {"message": _reply_message(message, reply)})

        @app.exception_handler(JsonRpcError)
        async def jsonrpc_error(_request: Request, error: JsonRpcError) -> JSONResponse:
            return _jsonrpc_error(None, error)

    else:
        send_path = f"{config.path}/message:send"

        @app.post(send_path, response_model=None)
        async def http_json_entry(request: Request) -> JSONResponse:
            if unauthorized := authorized(request):
                return unauthorized
            try:
                payload = await request.json()
            except ValueError:
                return _http_json_response(
                    {"error": {"code": "invalid_request", "message": "invalid JSON payload"}},
                    status_code=422,
                )
            if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
                return _http_json_response(
                    {
                        "error": {
                            "code": "invalid_request",
                            "message": "body must be an A2A SendMessageRequest object",
                        }
                    },
                    status_code=422,
                )
            message = payload["message"]
            try:
                text = extract_message_text(message.get("parts"))
            except ValueError as exc:
                return _http_json_response(
                    {"error": {"code": "invalid_request", "message": str(exc)}},
                    status_code=422,
                )
            if len(text) > config.max_message_chars:
                return _http_json_response(
                    {
                        "error": {
                            "code": "message_too_large",
                            "message": f"message text exceeds {config.max_message_chars} characters",
                        }
                    },
                    status_code=413,
                )
            reply, error_code = await invoke_message(text)
            if error_code:
                return _http_json_response(
                    {"error": {"code": error_code, "message": "workflow execution failed"}},
                    status_code=500,
                )
            return _http_json_response({"message": _reply_message(message, reply)})


__all__ = [
    "A2A_AGENT_CARD_PATH",
    "A2A_HTTP_JSON_MEDIA_TYPE",
    "A2A_PROTOCOL_VERSION",
    "A2A_SPEC_RELEASE",
    "A2AConfig",
    "JsonRpcError",
    "a2a_capabilities",
    "build_agent_card",
    "extract_message_text",
    "extract_output_text",
    "mount_a2a",
]
