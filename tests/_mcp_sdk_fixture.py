"""Independent pinned official SDK server, with an actual HTTP wire boundary."""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import mcp_types as types
import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount

METHOD_PARAMS = {
    "tools/list": {},
    "tools/call": {"name": "lookup", "arguments": {"value": 7}},
    "prompts/list": {},
    "prompts/get": {"name": "greet", "arguments": {"name": "Ada"}},
    "resources/list": {},
    "resources/read": {"uri": "https://fixture.test/resource"},
    "resources/templates/list": {},
}


@asynccontextmanager
async def sdk_http_fixture(
    *,
    network: bool = False,
    pagination: bool = False,
    input_required: bool = False,
    tool_handler=None,
    sse: bool = False,
    tls: tuple[tuple[str, str], str] | None = None,
):
    calls: list[dict[str, Any]] = []

    def page(params):
        if not pagination:
            return {}
        if params.cursor is None:
            return {"next_cursor": "opaque-page-2"}
        assert params.cursor == "opaque-page-2"
        return {}

    async def list_tools(context, params):
        return types.ListToolsResult(
            tools=[types.Tool(name="lookup", input_schema={"type": "object"})],
            **page(params),
            ttl_ms=0,
            cache_scope="public",
        )

    async def call_tool(context, params):
        if tool_handler is not None:
            return await tool_handler(context, params)
        if input_required:
            return types.InputRequiredResult(request_state="opaque-server-continuation")
        return types.CallToolResult(
            content=[types.TextContent(text="7")],
            structured_content={"value": 7},
        )

    async def list_prompts(context, params):
        return types.ListPromptsResult(
            prompts=[types.Prompt(name="greet")],
            **page(params),
            ttl_ms=0,
            cache_scope="public",
        )

    async def get_prompt(context, params):
        return types.GetPromptResult(
            messages=[types.PromptMessage(role="user", content=types.TextContent(text="Ada"))],
        )

    async def list_resources(context, params):
        return types.ListResourcesResult(
            resources=[types.Resource(uri="https://fixture.test/resource", name="resource")],
            **page(params),
            ttl_ms=0,
            cache_scope="public",
        )

    async def read_resource(context, params):
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(uri=params.uri, text="resource", mime_type="text/plain")
            ],
            ttl_ms=0,
            cache_scope="public",
        )

    async def list_templates(context, params):
        return types.ListResourceTemplatesResult(
            resource_templates=[
                types.ResourceTemplate(uri_template="https://fixture.test/{name}", name="named")
            ],
            **page(params),
            ttl_ms=0,
            cache_scope="public",
        )

    server = Server(
        "owa-sdk-fixture",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_prompts=list_prompts,
        on_get_prompt=get_prompt,
        on_list_resources=list_resources,
        on_read_resource=read_resource,
        on_list_resource_templates=list_templates,
    )
    manager = StreamableHTTPSessionManager(server, json_response=not sse, stateless=True)
    app = StreamableHTTPASGIApp(manager)

    async def capture(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        body = bytearray()
        original_receive = receive

        async def recorded_receive():
            frame = await original_receive()
            if frame["type"] == "http.request":
                body.extend(frame.get("body", b""))
            return frame

        await app(scope, recorded_receive, send)
        calls.append({"body": json.loads(body), "headers": dict(scope["headers"])})

    if not network:
        async with manager.run():
            yield httpx.ASGITransport(app=capture), calls, "https://fixture.test/mcp"
        return

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield

    network_app = Starlette(routes=[Mount("/mcp", app=capture)], lifespan=lifespan)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        tls_options = (
            {
                "ssl_certfile": tls[0][0],
                "ssl_keyfile": tls[0][1],
                "ssl_ca_certs": tls[1],
                "ssl_cert_reqs": ssl.CERT_REQUIRED,
            }
            if tls is not None
            else {}
        )
        server = uvicorn.Server(
            uvicorn.Config(
                network_app,
                host="127.0.0.1",
                port=port,
                log_level="critical",
                timeout_graceful_shutdown=2,
                **tls_options,
            )
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        try:
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline
                await asyncio.sleep(0.02)
            scheme = "https" if tls is not None else "http"
            yield None, calls, f"{scheme}://127.0.0.1:{port}/mcp/"
        finally:
            server.should_exit = True
            await asyncio.to_thread(thread.join, 5)
            assert not thread.is_alive(), "SDK HTTP fixture failed to stop"
