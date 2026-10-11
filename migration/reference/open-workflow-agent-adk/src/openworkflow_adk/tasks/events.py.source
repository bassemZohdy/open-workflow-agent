"""Builders for OpenAPI, AsyncAPI, A2A, and MCP calls."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from openworkflow_adk._utils import response_body
from openworkflow_adk.adk_compat import FunctionNode
from openworkflow_adk.durations import duration_seconds
from openworkflow_adk.errors import OpenWorkflowError
from openworkflow_adk.expressions import bind, evaluate
from openworkflow_adk.models import Task
from openworkflow_adk.resources.broker import Broker
from openworkflow_adk.security.security import guarded_async_client, validate_egress

from .call import _read_resource
from .simple import _dynamic


def _openapi_builder(name: str, task: Task) -> FunctionNode:
    async def call_openapi(ctx: Any) -> Any:
        arguments = bind(task.with_ or {}, ctx.state.to_dict())
        document = await _read_resource(arguments.get("document"))
        operation_id = arguments.get("operationId")
        operation = None
        path_template = None
        for path, path_item in document.get("paths", {}).items():
            for method, candidate in path_item.items():
                if isinstance(candidate, dict) and candidate.get("operationId") == operation_id:
                    operation = (method.upper(), candidate)
                    path_template = path
                    break
            if operation:
                break
        if operation is None:
            raise KeyError(f"OpenAPI operationId {operation_id!r} was not found")
        method, operation_spec = operation
        values = arguments.get("parameters", {})
        endpoint = (document.get("servers") or [{"url": ""}])[0].get("url", "") + path_template
        query: dict[str, Any] = {}
        headers: dict[str, str] = {}
        path_parameters = (
            document.get("paths", {}).get(path_template, {}).get("parameters", []) or []
        )
        operation_parameters = operation_spec.get("parameters", []) or []
        for parameter in [*path_parameters, *operation_parameters]:
            parameter_name = parameter.get("name")
            if parameter_name not in values:
                continue
            value = values[parameter_name]
            location = parameter.get("in")
            if location == "path":
                endpoint = endpoint.replace("{" + parameter_name + "}", str(value))
            elif location == "query":
                query[parameter_name] = value
            elif location == "header":
                headers[parameter_name] = str(value)
        body = values if operation_spec.get("requestBody") else None
        async with guarded_async_client(
            follow_redirects=bool(arguments.get("redirect", False))
        ) as client:
            validate_egress(endpoint)
            response = await client.request(
                method, endpoint, params=query, headers=headers, json=body
            )
            response.raise_for_status()
            output = arguments.get("output", "content")
            if output == "raw":
                return response.content
            if output == "response":
                return {
                    "status": response.status_code,
                    "headers": dict(response.headers),
                    "body": response_body(response),
                }
            return response_body(response)

    return FunctionNode(func=call_openapi, name=name)


def _asyncapi_builder(name: str, task: Task, broker: Broker | None) -> FunctionNode:
    async def call_asyncapi(ctx: Any) -> Any:
        if broker is None:
            raise ValueError("AsyncAPI calls require a broker adapter")
        arguments = bind(task.with_ or {}, ctx.state.to_dict())
        document = await _read_resource(arguments.get("document"))
        channel = arguments.get("channel")
        operation = arguments.get("operation")
        if not channel and operation:
            for candidate_channel, item in (document.get("channels") or {}).items():
                if isinstance(item, dict) and operation in item:
                    channel = candidate_channel
                    break
            for operation_name, item in (document.get("operations") or {}).items():
                if operation_name == operation and isinstance(item, dict):
                    action = item.get("action") or item.get("channel")
                    channel = action if isinstance(action, str) else channel
        if not channel:
            raise ValueError(f"AsyncAPI task {name!r} requires a channel or operation")
        message = arguments.get("message")
        if message is not None:
            if not isinstance(message, dict):
                raise ValueError("AsyncAPI message must be an object")
            await broker.publish(
                {
                    "channel": channel,
                    "data": message.get("payload"),
                    "headers": message.get("headers", {}),
                }
            )
            return message.get("payload")
        subscription = arguments.get("subscription") or {}
        filter_expression = subscription.get("filter") if isinstance(subscription, dict) else None
        timeout = _consume_timeout(task)
        while True:
            try:
                event = await asyncio.wait_for(broker.consume(), timeout)
            except asyncio.TimeoutError as error:
                raise TimeoutError(
                    f"AsyncAPI task {name!r} timed out waiting for channel {channel!r}"
                ) from error
            if event.get("channel") != channel:
                continue
            if filter_expression and not evaluate(
                filter_expression, {**ctx.state.to_dict(), "event": event}
            ):
                continue
            return event.get("data")

    return _dynamic(FunctionNode(func=call_asyncapi, name=name))


def _consume_timeout(task: Task) -> float:
    """Resolve the consumer wait budget from the task timeout or the environment."""
    configured = task.timeout
    if isinstance(configured, dict):
        after = configured.get("after")
        return duration_seconds(after) if after is not None else duration_seconds(configured)
    if isinstance(configured, (int, float)):
        return float(configured)
    if isinstance(configured, str) and configured:
        return duration_seconds(configured)
    default = float(os.environ.get("WORKFLOW_CONSUME_TIMEOUT_SECONDS", "3600"))
    return default


def _endpoint_uri(endpoint: Any) -> str | None:
    if isinstance(endpoint, str):
        return endpoint
    if isinstance(endpoint, dict):
        return endpoint.get("uri")
    return None


def _a2a_builder(name: str, task: Task) -> FunctionNode:
    async def call_a2a(ctx: Any) -> Any:
        arguments = bind(task.with_ or {}, ctx.state.to_dict())
        server = _endpoint_uri(arguments.get("server"))
        if not server and arguments.get("agentCard"):
            card = await _read_resource(arguments["agentCard"])
            server = card.get("url") or card.get("supportedInterfaces", [{}])[0].get("url")
        if not server:
            raise ValueError(f"A2A task {name!r} requires with.server or with.agentCard")
        validate_egress(server)
        method = arguments.get("method", "message/send")
        request = {
            "jsonrpc": "2.0",
            "id": name,
            "method": method,
            "params": arguments.get("parameters", {}),
        }
        async with guarded_async_client() as client:
            response = await client.post(server, json=request)
            response.raise_for_status()
            result = response.json()
        if "error" in result:
            raise OpenWorkflowError(detail=str(result["error"]), title="A2A request failed")
        return result.get("result")

    return _dynamic(FunctionNode(func=call_a2a, name=name))


async def _mcp_stdio_call(process: dict[str, Any], request: dict[str, Any]) -> Any:
    command = [process["command"], *(process.get("arguments") or [])]
    _check_mcp_command(process["command"])
    environment = {
        key: value
        for key, value in {**os.environ, **(process.get("environment") or {})}.items()
        if not key.startswith("WORKFLOW_SECRET__")
    }
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    assert proc.stdin is not None and proc.stdout is not None
    try:
        proc.stdin.write((json.dumps(request) + "\n").encode())
        await proc.stdin.drain()
        line = await proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP stdio server returned no JSON-RPC response")
        return json.loads(line)
    finally:
        proc.stdin.close()
        await _terminate_mcp_process(proc)


def _check_mcp_command(command: str) -> None:
    """Reject MCP stdio commands that are not on the configured allowlist.

    MCP stdio servers execute a document- or state-controlled command, so the
    set of permitted commands must be explicit. Configure
    ``WORKFLOW_MCP_COMMAND_ALLOWLIST`` as a comma-separated list of executable
    names or paths; set ``WORKFLOW_MCP_ALLOW_UNLISTED=1`` to disable the check
    (not recommended on untrusted boundaries).
    """
    if os.environ.get("WORKFLOW_MCP_ALLOW_UNLISTED", "").lower() in {"1", "true", "yes"}:
        return
    allowlist = {
        item.strip()
        for item in os.environ.get("WORKFLOW_MCP_COMMAND_ALLOWLIST", "").split(",")
        if item.strip()
    }
    if not allowlist:
        raise PermissionError(
            "MCP stdio servers are disabled: set WORKFLOW_MCP_COMMAND_ALLOWLIST "
            "to the allowed server command(s)"
        )
    name = command.split()[0]
    if name not in allowlist and os.path.basename(name) not in allowlist:
        raise PermissionError(
            f"MCP stdio command {name!r} is not on WORKFLOW_MCP_COMMAND_ALLOWLIST"
        )


async def _terminate_mcp_process(proc: Any) -> None:
    """Kill an MCP stdio process, bounded by the configured timeout."""
    timeout = float(os.environ.get("WORKFLOW_MCP_TIMEOUT_SECONDS", "60"))
    try:
        await asyncio.wait_for(proc.wait(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await asyncio.wait_for(proc.wait(), 5)


def _mcp_builder(name: str, task: Task) -> FunctionNode:
    async def call_mcp(ctx: Any) -> Any:
        arguments = bind(task.with_ or {}, ctx.state.to_dict())
        transport = arguments.get("transport") or {}
        method = arguments.get("method")
        if not method:
            raise ValueError(f"MCP task {name!r} requires with.method")
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": arguments.get("parameters", {}),
        }
        if "http" in transport:
            configuration = transport["http"]
            endpoint = _endpoint_uri(configuration.get("endpoint"))
            if not endpoint:
                raise ValueError("MCP HTTP transport requires endpoint.uri")
            validate_egress(endpoint)
            headers = dict(configuration.get("headers") or {})
            async with guarded_async_client() as client:
                initialized = await client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": "initialize",
                        "method": "initialize",
                        "params": {"protocolVersion": arguments.get("protocolVersion")},
                    },
                )
                initialized.raise_for_status()
                response = await client.post(endpoint, headers=headers, json=request)
                response.raise_for_status()
                result = response.json()
        elif "stdio" in transport:
            initialized = await _mcp_stdio_call(
                transport["stdio"],
                {
                    "jsonrpc": "2.0",
                    "id": "initialize",
                    "method": "initialize",
                    "params": {"protocolVersion": arguments.get("protocolVersion")},
                },
            )
            del initialized
            result = await _mcp_stdio_call(transport["stdio"], request)
        else:
            raise ValueError("MCP transport must define http or stdio")
        if "error" in result:
            raise OpenWorkflowError(detail=str(result["error"]), title="MCP request failed")
        return result.get("result")

    return _dynamic(FunctionNode(func=call_mcp, name=name))
