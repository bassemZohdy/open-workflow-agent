"""Common observation HTTP boundaries are testable without a native SDK."""

from __future__ import annotations

import json

import httpx
import pytest
from native_api_test_support import runtime
from open_workflow_agent.execution_authority import authority_scope
from open_workflow_agent.native_extensions import NativeExtensionRequirement
from open_workflow_agent.native_stream_api import mount_native_streams, native_stream_capabilities


def test_capability_document_only_advertises_enabled_checkpoint_profile():
    baseline = native_stream_capabilities()
    enabled = native_stream_capabilities(checkpoints=True)
    assert "checkpoints" not in baseline["modes"]
    assert "checkpointModeProfile" not in baseline
    assert enabled["modes"] == baseline["modes"] + ["checkpoints"]
    assert enabled["checkpointModeProfile"] == "langgraph.streams.checkpoints@1.0.0"
    assert enabled["disconnectCancelsInvocation"] is False
    assert "messages" not in enabled["modes"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "success",
        "replay",
        "capacity",
        "unavailable",
        "unsupported",
        "missing_registry",
        "other_owner",
        "anonymous",
    ],
)
async def test_observer_owner_replay_capacity_and_capability_boundaries(tmp_path, case):
    with runtime(tmp_path) as fixture:
        services, engine, handle, actors, app, authorize, invocation, calls = fixture
        mount_native_streams(app, services, engine, authorize, invocation)
        registry = services.native_streams
        log = registry.begin(handle, (NativeExtensionRequirement("langgraph.streams", "1.0.0"),))
        first = log.append("values", {"private": "alice"}, scope="/do")
        log.append("updates", {"done": True}, scope="/do", task_reference="/do/0/noop")
        registry.end(handle, "completed")
        selected = actors["alice"]
        headers = {}
        if case == "replay":
            headers["Last-Event-ID"] = "invalid-cursor"
        elif case == "capacity":
            _, held = registry.subscribe(handle)
        elif case == "unavailable":
            registry._entries.pop(handle.invocation_id).log.close()
        elif case == "unsupported":
            engine.extensions = ()
        elif case == "missing_registry":
            services.native_streams = None
        elif case == "other_owner":
            selected = actors["bob"]
        elif case == "anonymous":
            selected = None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            with authority_scope(selected):
                response = await client.get(
                    f"/v1/invocations/{handle.invocation_id}/stream",
                    headers=headers,
                    params={"timeout_seconds": 0.1, "mode": "updates"},
                )
        if case == "success":
            assert response.status_code == 200
            frames = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert len(frames) == 2
            assert frames[0]["mode"] == "updates" and frames[0]["data"] == {"done": True}
            assert first.event_id not in response.text and "private" not in response.text
            assert not registry._consumers
        else:
            expected = (
                404
                if case in {"other_owner", "anonymous"}
                else 429
                if case == "capacity"
                else 422
                if case in {"unsupported", "missing_registry"}
                else 409
            )
            assert response.status_code == expected, response.text
            assert "private" not in response.text
        if case in {"other_owner", "anonymous"}:
            assert calls == []
        else:
            assert calls == [("invocations.stream", "invocation:" + handle.invocation_id)]
        if case == "capacity":
            registry.release(held)
        registry.close()
        assert services.model.calls == []
        assert services.invocations.get(handle.invocation_id).status == "running"
