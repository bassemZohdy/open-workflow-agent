from __future__ import annotations

import pytest
from open_workflow_agent.config import NativeStreamsConfig, RuntimeConfig
from open_workflow_agent.errors import ConfigurationError
from open_workflow_agent.native_extensions import NativeExtensionRequirement
from open_workflow_agent.native_stream_registry import NativeStreamRegistry

REQUIRED = (NativeExtensionRequirement("langgraph.streams", "1.0.0"),)


def handle(services, name="alice"):
    return services.invocations.create(
        engine="langgraph",
        session_id="same",
        user_id="same",
        owner_principal=name,
        workflow_name="stream",
        workflow_version="1.0.0",
        workflow_fingerprint="a" * 64,
    )


def test_absent_stream_configuration_preserves_legacy_serialization():
    config = RuntimeConfig()
    assert config.observability.native_streams is None
    assert "native_streams" not in config.model_dump(mode="json")["observability"]
    with pytest.raises(ValueError):
        RuntimeConfig.model_validate({"observability": {"native_streams": {}}})


@pytest.mark.parametrize(
    "bounds",
    [
        {"max_logs": True},
        {"max_logs": 0},
        {"max_subscribers": 129},
        {"max_event_bytes": 255},
        {"max_event_bytes": 1024, "max_bytes": 512},
        {"retention_seconds": float("nan")},
        {"retention_seconds": float("inf")},
        {"max_logs": 128, "max_bytes": 1_048_576},
        {"max_subscribers": 128, "max_bytes": 1_048_576, "queue_size": 1},
        {"max_subscribers": 16, "queue_size": 1000},
    ],
)
def test_invalid_and_aggregate_resource_budgets_are_rejected(bounds):
    with pytest.raises(ValueError):
        NativeStreamsConfig(**bounds)


@pytest.mark.asyncio
async def test_finish_drains_observations_and_resume_keeps_cursor_without_reopening_readers(
    services,
):
    registry = NativeStreamRegistry(NativeStreamsConfig())
    owner = handle(services)
    log = registry.begin(owner, REQUIRED)
    _, live = registry.subscribe(owner)
    first = log.append("values", {"value": 1}, scope="/do")
    registry.end(owner, "waiting")
    assert await live.receive(timeout=1) == first
    with pytest.raises(EOFError, match="waiting"):
        await live.receive(timeout=1)
    assert registry.begin(owner, REQUIRED) is log
    next_frame = log.append("values", {"value": 2}, scope="/do")
    with pytest.raises(EOFError, match="waiting"):
        await live.receive(timeout=1)
    backlog, resumed = registry.subscribe(owner, last_event_id=first.event_id)
    assert backlog == (next_frame,)
    registry.release(live)
    registry.release(resumed)
    registry.close()


def test_active_capacity_skips_observation_and_evicted_response_keeps_consumer_slot(services):
    registry = NativeStreamRegistry(NativeStreamsConfig(max_logs=1, max_subscribers=1))
    first, second = handle(services), handle(services)
    log = registry.begin(first, REQUIRED)
    log.append("values", None, scope="/do")
    _, consumer = registry.subscribe(first)
    assert registry.begin(second, REQUIRED) is None
    registry.end(first, "completed")
    assert registry.begin(second, REQUIRED) is not None
    with pytest.raises(LookupError):
        registry.subscribe(first)
    with pytest.raises(RuntimeError, match="capacity"):
        registry.subscribe(second)
    registry.release(consumer)
    _, replacement = registry.subscribe(second)
    registry.release(replacement)
    registry.close()


def test_expiry_and_changed_owner_or_definition_cannot_reuse_observations(services, monkeypatch):
    import open_workflow_agent.native_stream_registry as module

    clock = [10.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    registry = NativeStreamRegistry(NativeStreamsConfig(retention_seconds=1))
    owner = handle(services)
    registry.begin(owner, REQUIRED).append("values", {"private": True}, scope="/do")
    registry.end(owner, "completed")
    original = owner.owner_principal
    owner.owner_principal = "other"
    with pytest.raises(LookupError):
        registry.subscribe(owner)
    owner.owner_principal = original
    clock[0] = 11.1
    with pytest.raises(LookupError):
        registry.subscribe(owner)
    registry.close()


def test_undeclared_optional_and_changed_installations_do_not_enable_streams(services):
    config = NativeStreamsConfig()
    registry = NativeStreamRegistry(config)
    owner = handle(services)
    assert registry.begin(owner, ()) is None
    assert (
        registry.begin(owner, (NativeExtensionRequirement("langgraph.streams", "1.0.0", False),))
        is None
    )
    config.max_logs = 1
    with pytest.raises(ConfigurationError):
        registry.verify(config)
    registry.close()


@pytest.mark.asyncio
async def test_native_write_worker_selects_compatible_loop_without_global_policy_changes():
    import asyncio
    import sys
    import threading

    from open_workflow_agent.ownership import run_native_storage_write

    caller = asyncio.get_running_loop()
    caller_thread = threading.get_ident()
    previous = asyncio.get_event_loop_policy()
    incompatible = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    try:
        if sys.platform == "win32" and incompatible is not None:
            asyncio.set_event_loop_policy(incompatible())

        async def operation():
            loop = asyncio.get_running_loop()
            assert loop is not caller and threading.get_ident() != caller_thread
            assert isinstance(loop, asyncio.SelectorEventLoop)
            return "committed"

        assert await run_native_storage_write(operation) == "committed"
        if sys.platform == "win32" and incompatible is not None:
            assert isinstance(asyncio.get_event_loop_policy(), incompatible)
    finally:
        asyncio.set_event_loop_policy(previous)
