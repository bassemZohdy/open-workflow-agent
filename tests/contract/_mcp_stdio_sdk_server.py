"""Deterministic actual pinned SDK server; the common sandbox owns its process."""

from __future__ import annotations

import json
import os
from pathlib import Path

import anyio
import mcp_types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server


async def tools(_context, _parameters):
    return types.ListToolsResult(tools=[types.Tool(name="echo", input_schema={"type": "object"})])


async def call(_context, parameters):
    value = parameters.arguments or {}
    if ledger := os.getenv("LEDGER"):
        with Path(ledger).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "input": value}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(value, sort_keys=True))],
        structured_content=value,
    )


async def prompts(_context, _parameters):
    return types.ListPromptsResult(prompts=[types.Prompt(name="hello")])


async def prompt(_context, _parameters):
    return types.GetPromptResult(
        messages=[
            types.PromptMessage(role="user", content=types.TextContent(type="text", text="hello"))
        ]
    )


async def resources(_context, _parameters):
    return types.ListResourcesResult(
        resources=[types.Resource(uri="test://resource", name="resource")]
    )


async def templates(_context, _parameters):
    return types.ListResourceTemplatesResult(
        resource_templates=[types.ResourceTemplate(uri_template="test://{name}", name="template")]
    )


async def resource(_context, _parameters):
    return types.ReadResourceResult(
        contents=[
            types.TextResourceContents(
                uri="test://resource", text="retained", mime_type="text/plain"
            )
        ]
    )


async def run() -> None:
    server = Server(
        "bounded-fixture",
        version="1.0.0",
        on_list_tools=tools,
        on_call_tool=call,
        on_list_prompts=prompts,
        on_get_prompt=prompt,
        on_list_resources=resources,
        on_list_resource_templates=templates,
        on_read_resource=resource,
    )
    # A witness is confined to the execution workspace, never runtime data.
    Path("server-witness.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    async with stdio_server() as (incoming, outgoing):
        await server.run(incoming, outgoing, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(run)
