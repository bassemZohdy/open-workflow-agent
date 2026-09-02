"""Bounded inbound A2A 1.0 profile behind a deployment-selected binding.

The runtime targets A2A protocol release 1.0.1 and advertises protocol version
1.0 on each AgentInterface. Disabled by default. When enabled, the runtime
exposes standard Agent Card discovery, synchronous SendMessage, and a bounded
Task projection over common OWA invocation state.

Two bindings are selectable through deployment configuration:

- ``jsonrpc``: JSON-RPC 2.0 over HTTP using A2A v1 PascalCase methods.
- ``http_json``: A2A HTTP+JSON using the standard REST endpoint/media type.

Streaming, resubscription, push notifications, and extended Agent Cards remain
outside this bounded slice.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .a2a_tasks import project_a2a_task
from .config import A2AConfig
from .security import (
    AuthorizationPolicy,
    BearerSecurityProfile,
    Principal,
    SecurityConfig,
    authorize,
    resolve_secret,
    static_principal,
)

A2A_SPEC_RELEASE = "1.0.1"
A2A_PROTOCOL_VERSION = "1.0"
A2A_AGENT_CARD_PATH = "/.well-known/agent-card.json"
A2A_HTTP_JSON_MEDIA_TYPE = "application/a2a+json"

_MAX_TEXT_PARTS = 64
_TRANSPORT_LABELS = {"jsonrpc": "JSONRPC", "http_json": "HTTP+JSON"}
_TERMINAL_TASK_STATUSES = frozenset({"completed", "faulted", "cancelled"})


def a2a_capabilities(config: A2AConfig) -> dict[str, Any]:
    """Return the sanitized ``features.a2a`` capability block."""

    skills = [skill.id for skill in config.skills]
    return {
        "enabled": config.enabled,
        "specRelease": A2A_SPEC_RELEASE if config.enabled else None,
        "protocolVersion": A2A_PROTOCOL_VERSION if config.enabled else None,
        "transport": config.transport,
        "card": A2A_AGENT_CARD_PATH if config.enabled else None,
        "streaming": False,
        "pushNotifications": False,
        "tasks": config.enabled,
        "taskOperations": ["GetTask", "CancelTask"] if config.enabled else [],
        "skills": skills if config.enabled else [],
        "auth": "bearer" if config.enabled and config.security_profile else None,
        "authorization": bool(
            config.enabled and config.authorization and config.authorization.rules
        ),
    }


def build_agent_card(
    config: A2AConfig,
    *,
    url: str,
    workflow_name: str,
    skills: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the bounded A2A v1 Agent Card for the configured workflow(s)."""

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
        "skills": skills
        or [
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
    TASK_NOT_FOUND = -32001
    TASK_NOT_CANCELABLE = -32002
    VERSION_NOT_SUPPORTED = -32009

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


_IMPLICIT_SKILL_RESOURCE = "skill:workflow"
_TASK_RESOURCE = "tasks"


def _authenticate(
    config: A2AConfig, security: SecurityConfig, request: Request
) -> Principal | None:
    if not config.security_profile:
        return Principal(identity="anonymous")
    profile = security.profile(config.security_profile)
    if not isinstance(profile, BearerSecurityProfile):
        return None
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    try:
        expected = resolve_secret(profile.token)
    except ValueError:
        return None
    if not hmac.compare_digest(authorization.removeprefix("Bearer ").strip(), expected):
        return None
    return static_principal(profile)


def _permitted(
    principal: Principal, policy: AuthorizationPolicy | None, *, action: str, resource: str
) -> bool:
    if policy is None or not policy.rules:
        return True
    return authorize(principal, policy, action=action, resource=resource)


def _requested_protocol_version(request: Request) -> str | None:
    return request.headers.get("a2a-version") or request.query_params.get("A2A-Version")


def _reply_message(source_message: dict[str, Any], reply: str) -> dict[str, Any]:
    source_id = source_message.get("messageId")
    return {
        "messageId": f"a2a-{source_id}-reply" if isinstance(source_id, str) else "a2a-reply",
        "role": "ROLE_AGENT",
        "parts": [{"text": reply}],
    }


def _requested_skill_id(params: Any, message: Any) -> str | None:
    """Read the deployment-declared skill selector from A2A metadata."""

    for source in (message.get("metadata") if isinstance(message, Mapping) else None, params):
        if isinstance(source, Mapping):
            value = source.get("skillId")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _task_id(params: Any) -> str:
    if not isinstance(params, dict) or not isinstance(params.get("id"), str) or not params["id"]:
        raise JsonRpcError(JsonRpcError.INVALID_PARAMS, "task operation requires params.id")
    return str(params["id"])


@dataclass(frozen=True, slots=True)
class A2ASendOutcome:
    """Result of one SendMessage execution, transport-independent.

    Exactly one of ``reply_text``, ``task``, or ``error_code`` is set.
    ``task`` carries a sanitized A2A Task projection (used for
    ``returnImmediately`` sends and for sends that end in ``input-required``).
    """

    reply_text: str | None = None
    task: dict[str, Any] | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ResumeTarget:
    """Deployment-side view of an existing task for a resuming SendMessage."""

    status: str
    skill_id: str | None
    deployable: bool


def _get_task(app: FastAPI, task_id: str) -> dict[str, Any]:
    handle = app.state.services.invocations.get(task_id)
    if handle is None:
        raise JsonRpcError(JsonRpcError.TASK_NOT_FOUND, "task not found")
    return project_a2a_task(handle)


async def _cancel_task(app: FastAPI, task_id: str) -> dict[str, Any]:
    handle = app.state.services.invocations.get(task_id)
    if handle is None:
        raise JsonRpcError(JsonRpcError.TASK_NOT_FOUND, "task not found")
    if handle.status in _TERMINAL_TASK_STATUSES:
        raise JsonRpcError(JsonRpcError.TASK_NOT_CANCELABLE, "task cannot be canceled")
    result = await app.state.engine.cancel(handle, operation_id=f"a2a:cancel:{task_id}")
    refreshed = app.state.services.invocations.get(result.invocation_id)
    if refreshed is None:
        raise JsonRpcError(JsonRpcError.TASK_NOT_FOUND, "task not found")
    return project_a2a_task(refreshed)


def mount_a2a(
    app: FastAPI,
    config: A2AConfig,
    security: SecurityConfig,
    *,
    start_task: Callable[[str, str | None, bool], Awaitable[A2ASendOutcome]],
    resolve_resume_target: Callable[[str], Awaitable[ResumeTarget | None]],
    resume_task: Callable[[str, str, bool], Awaitable[A2ASendOutcome]],
    skills: list[dict[str, Any]] | None = None,
) -> None:
    """Mount the selected bounded A2A v1 binding on the application.

    ``start_task(text, skill_id, return_immediately)`` executes the mapped
    workflow for a new task. ``resolve_resume_target(task_id)`` returns the
    deployment-side view of an existing task (``None`` when unknown), and
    ``resume_task(text, task_id, return_immediately)`` resumes a waiting task
    through the common resume contract. Outcomes carry a sanitized reply, a
    Task projection, or a sanitized error code — never engine exceptions.
    """

    if not config.enabled:
        return

    def send_error(
        request_id: Any,
        *,
        code: str,
        message: str,
        status: int,
        rpc_code: int = JsonRpcError.APPLICATION_ERROR,
        details: Any = None,
    ) -> JSONResponse:
        if config.transport == "jsonrpc":
            # JSON-RPC reports protocol-level failures in the error envelope at
            # HTTP 200; only transport-layer gates (401/403) use HTTP statuses.
            del status
            return _jsonrpc_error(
                request_id,
                JsonRpcError(rpc_code, message, details=details),
            )
        return _http_json_response(
            {"error": {"code": code, "message": message}},
            status_code=status,
        )

    def gate(request: Request) -> tuple[Principal | None, JSONResponse | None]:
        principal = _authenticate(config, security, request)
        if principal is not None:
            return principal, None
        if config.transport == "jsonrpc":
            return None, _jsonrpc_error(
                None,
                JsonRpcError(
                    JsonRpcError.APPLICATION_ERROR,
                    "unauthorized",
                    http_status=401,
                ),
            )
        return None, _http_json_response(
            {"error": {"code": "unauthorized", "message": "a2a authorization failed"}},
            status_code=401,
        )

    def forbidden(request_id: Any = None) -> JSONResponse:
        if config.transport == "jsonrpc":
            return _jsonrpc_error(
                request_id,
                JsonRpcError(
                    JsonRpcError.APPLICATION_ERROR,
                    "forbidden",
                    http_status=403,
                ),
            )
        return _http_json_response(
            {"error": {"code": "forbidden", "message": "not authorized for this operation"}},
            status_code=403,
        )

    def send_permitted(principal: Principal, skill_id: str | None) -> bool:
        return _permitted(
            principal,
            config.authorization,
            action="message.send",
            resource=f"skill:{skill_id}" if skill_id else _IMPLICIT_SKILL_RESOURCE,
        )

    def task_permitted(principal: Principal, action: str) -> bool:
        return _permitted(principal, config.authorization, action=action, resource=_TASK_RESOURCE)

    async def handle_send(
        principal: Principal, params: Any, *, request_id: Any = None
    ) -> JSONResponse:
        if not isinstance(params, dict) or not isinstance(params.get("message"), dict):
            return send_error(
                request_id,
                code="invalid_request",
                message="SendMessage requires a message object",
                status=422,
                rpc_code=JsonRpcError.INVALID_PARAMS,
            )
        message = params["message"]
        try:
            text = extract_message_text(message.get("parts"))
        except ValueError as exc:
            return send_error(
                request_id,
                code="invalid_request",
                message=str(exc),
                status=422,
                rpc_code=JsonRpcError.INVALID_PARAMS,
            )
        if len(text) > config.max_message_chars:
            return send_error(
                request_id,
                code="message_too_large",
                message=f"message text exceeds {config.max_message_chars} characters",
                status=413,
                rpc_code=JsonRpcError.INVALID_PARAMS,
            )
        configuration = params.get("configuration")
        return_immediately = bool(
            isinstance(configuration, Mapping) and configuration.get("returnImmediately") is True
        )

        task_ref = message.get("taskId")
        if isinstance(task_ref, str) and task_ref.strip():
            target = await resolve_resume_target(task_ref.strip())
            if target is None:
                return send_error(
                    request_id,
                    code="task_not_found",
                    message="task not found",
                    status=404,
                    rpc_code=JsonRpcError.TASK_NOT_FOUND,
                )
            if not target.deployable:
                if config.authorization and config.authorization.rules:
                    return forbidden(request_id)
                return send_error(
                    request_id,
                    code="task_not_resumeable",
                    message="task is not accepting input",
                    status=409,
                )
            if target.status != "waiting":
                return send_error(
                    request_id,
                    code="task_not_accepting_input",
                    message="task is not accepting input",
                    status=409,
                )
            if not send_permitted(principal, target.skill_id):
                return forbidden(request_id)
            outcome = await resume_task(text, task_ref.strip(), return_immediately)
        else:
            skill_id = _requested_skill_id(params, message)
            if config.skills and skill_id not in {skill.id for skill in config.skills}:
                return send_error(
                    request_id,
                    code="skill_not_found",
                    message=f"unknown skill: {skill_id}"
                    if skill_id
                    else "message requires a skillId",
                    status=422,
                    rpc_code=JsonRpcError.INVALID_PARAMS,
                )
            if not send_permitted(principal, skill_id):
                return forbidden(request_id)
            outcome = await start_task(text, skill_id, return_immediately)

        if outcome.error_code:
            return send_error(
                request_id,
                code=outcome.error_code,
                message="workflow execution failed",
                status=500,
                details={"code": outcome.error_code},
            )
        if outcome.task is not None:
            body: dict[str, Any] = {"task": outcome.task}
        else:
            body = {"message": _reply_message(message, outcome.reply_text or "")}
        if config.transport == "jsonrpc":
            return _jsonrpc_response(request_id, body)
        return _http_json_response(body)

    def version_supported(request: Request) -> JSONResponse | None:
        requested = _requested_protocol_version(request)
        if requested == A2A_PROTOCOL_VERSION:
            return None
        if config.transport == "jsonrpc":
            return _jsonrpc_error(
                None,
                JsonRpcError(
                    JsonRpcError.VERSION_NOT_SUPPORTED,
                    "A2A protocol version is not supported",
                    details={"supportedVersion": A2A_PROTOCOL_VERSION},
                ),
            )
        return _http_json_response(
            {
                "error": {
                    "code": "version_not_supported",
                    "message": "A2A protocol version is not supported",
                    "supportedVersion": A2A_PROTOCOL_VERSION,
                }
            },
            status_code=400,
        )

    @app.get(A2A_AGENT_CARD_PATH, response_model=None)
    async def agent_card(request: Request) -> JSONResponse | dict[str, Any]:
        _, unauthorized = gate(request)
        if unauthorized is not None:
            return unauthorized
        base = config.public_base_url or str(request.base_url).rstrip("/")
        return build_agent_card(
            config,
            url=f"{base}{config.path}",
            workflow_name=str(getattr(app.state, "workflow_name", config.agent_name)),
            skills=skills or getattr(app.state, "a2a_skill_cards", None),
        )

    if config.transport == "jsonrpc":

        @app.post(config.path)
        async def jsonrpc_entry(request: Request) -> JSONResponse:
            principal, unauthorized = gate(request)
            if unauthorized is not None:
                return unauthorized
            assert principal is not None
            if unsupported := version_supported(request):
                return unsupported
            request_id: Any = None
            try:
                try:
                    envelope = await request.json()
                except ValueError as exc:
                    raise JsonRpcError(JsonRpcError.PARSE_ERROR, "invalid JSON payload") from exc
                if not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0":
                    raise JsonRpcError(JsonRpcError.INVALID_REQUEST, "not a JSON-RPC 2.0 request")
                request_id = envelope.get("id")
                method = envelope.get("method")
                params = envelope.get("params")
                if method == "GetTask":
                    if not task_permitted(principal, "tasks.get"):
                        return forbidden(request_id)
                    return _jsonrpc_response(request_id, _get_task(app, _task_id(params)))
                if method == "CancelTask":
                    if not task_permitted(principal, "tasks.cancel"):
                        return forbidden(request_id)
                    return _jsonrpc_response(
                        request_id,
                        await _cancel_task(app, _task_id(params)),
                    )
                if method != "SendMessage":
                    raise JsonRpcError(
                        JsonRpcError.METHOD_NOT_FOUND,
                        "A2A method is not supported in the bounded profile",
                    )
                return await handle_send(principal, params, request_id=request_id)
            except JsonRpcError as error:
                return _jsonrpc_error(request_id, error)

    else:
        send_path = f"{config.path}/message:send"
        task_path = f"{config.path}/tasks/{{task_id}}"
        cancel_task_path = f"{config.path}/tasks/{{task_id}}:cancel"

        @app.post(send_path, response_model=None)
        async def http_json_entry(request: Request) -> JSONResponse:
            principal, unauthorized = gate(request)
            if unauthorized is not None:
                return unauthorized
            assert principal is not None
            if unsupported := version_supported(request):
                return unsupported
            try:
                payload = await request.json()
            except ValueError:
                return _http_json_response(
                    {"error": {"code": "invalid_request", "message": "invalid JSON payload"}},
                    status_code=422,
                )
            if not isinstance(payload, dict):
                return _http_json_response(
                    {
                        "error": {
                            "code": "invalid_request",
                            "message": "body must be an A2A SendMessageRequest object",
                        }
                    },
                    status_code=422,
                )
            return await handle_send(principal, payload)

        @app.get(task_path, response_model=None)
        async def http_json_get_task(task_id: str, request: Request) -> JSONResponse:
            principal, unauthorized = gate(request)
            if unauthorized is not None:
                return unauthorized
            assert principal is not None
            if not task_permitted(principal, "tasks.get"):
                return forbidden()
            if unsupported := version_supported(request):
                return unsupported
            try:
                return _http_json_response(_get_task(app, task_id))
            except JsonRpcError as error:
                return _http_json_response(
                    {"error": {"code": "task_not_found", "message": error.message}},
                    status_code=404,
                )

        @app.post(cancel_task_path, response_model=None)
        async def http_json_cancel_task(task_id: str, request: Request) -> JSONResponse:
            principal, unauthorized = gate(request)
            if unauthorized is not None:
                return unauthorized
            assert principal is not None
            if not task_permitted(principal, "tasks.cancel"):
                return forbidden()
            if unsupported := version_supported(request):
                return unsupported
            try:
                return _http_json_response(await _cancel_task(app, task_id))
            except JsonRpcError as error:
                if error.code == JsonRpcError.TASK_NOT_FOUND:
                    code = "task_not_found"
                    status_code = 404
                else:
                    code = "task_not_cancelable"
                    status_code = 400
                return _http_json_response(
                    {"error": {"code": code, "message": error.message}},
                    status_code=status_code,
                )


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
