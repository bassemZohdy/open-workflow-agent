from __future__ import annotations

import asyncio
from time import monotonic

import pytest
from open_workflow_agent.config import NativeStreamsConfig
from open_workflow_agent.native_extensions import NativeExtensionRequirement
from open_workflow_agent.native_stream_api import _NativeStreamResponse, _stream
from open_workflow_agent.native_stream_registry import NativeStreamRegistry
from open_workflow_agent.streaming import StreamLimits


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["deadline", "cancel", "send_error"])
async def test_blocked_or_disconnected_network_releases_slot_and_leaves_producer_usable(
    services, stop
):
    registry = NativeStreamRegistry(NativeStreamsConfig(max_subscribers=1))
    owner = services.invocations.create(
        engine="langgraph",
        session_id="same",
        user_id="same",
        owner_principal="alice",
        workflow_name="stream",
        workflow_version="1.0.0",
        workflow_fingerprint="a" * 64,
    )
    log = registry.begin(owner, (NativeExtensionRequirement("langgraph.streams", "1.0.0"),))
    log.append("values", {"private": True}, scope="/do")
    backlog, subscription = registry.subscribe(owner)
    limits = StreamLimits(timeout_seconds=0.05)
    response = _NativeStreamResponse(
        _stream(backlog, subscription, limits, "all"), registry, subscription, 0.05
    )
    reached = asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.body":
            reached.set()
            if stop == "send_error":
                raise OSError("controlled disconnect")
            await asyncio.Event().wait()

    async def receive():
        await asyncio.Event().wait()

    started = monotonic()
    task = asyncio.create_task(
        response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    )
    await asyncio.wait_for(reached.wait(), timeout=1)
    if stop == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif stop == "send_error":
        from starlette.requests import ClientDisconnect

        with pytest.raises(ClientDisconnect):
            await task
    else:
        await task
        assert monotonic() - started < 1
    assert not registry._consumers
    log.append("values", {"still_running": True}, scope="/do")
    _, next_consumer = registry.subscribe(owner)
    registry.release(next_consumer)
    registry.close()
