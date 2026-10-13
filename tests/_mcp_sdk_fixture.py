"""Independent pinned official SDK server, with an actual HTTP wire boundary."""

from __future__ import annotations

import asyncio
import json
import socket
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
async def sdk_http_fixture(*, network: bool = False):
    calls: list[dict[str, Any]] = []

    async def list_tools(context, params):
        return types.ListToolsResult(
            tools=[types.Tool(name="lookup", input_schema={"type": "object"})],
            ttl_ms=0,
            cache_scope="public",
        )

    async def call_tool(context, params):
        return types.CallToolResult(
            content=[types.TextContent(text="7")],
            structured_content={"value": 7},
        )

    async def list_prompts(context, params):
        return types.ListPromptsResult(
            prompts=[types.Prompt(name="greet")],
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
    manager = StreamableHTTPSessionManager(server, json_response=True, stateless=True)
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
        server = uvicorn.Server(
            uvicorn.Config(
                network_app,
                host="127.0.0.1",
                port=port,
                log_level="critical",
                timeout_graceful_shutdown=2,
            )
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        try:
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline
                await asyncio.sleep(0.02)
            yield None, calls, f"http://127.0.0.1:{port}/mcp/"
        finally:
            server.should_exit = True
            await asyncio.to_thread(thread.join, 5)
            assert not thread.is_alive(), "SDK HTTP fixture failed to stop"
