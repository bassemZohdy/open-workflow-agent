"""Opt-in composition and declaration guards; no remote transport qualification."""

from __future__ import annotations

import json

import pytest
from open_workflow_agent.agent_roles import RoleDescriptor
from open_workflow_agent.bounded_memory import BoundedMemoryService
from open_workflow_agent.catalog import BoundedModel, CatalogContext, FakeModel
from open_workflow_agent.config import AgentRoleConfig, MemoryBoundsConfig, RuntimeConfig
from open_workflow_agent.errors import ConfigurationError, ToolError, WorkflowExecutionError
from open_workflow_agent.services import RuntimeServices
from pydantic import ValidationError


def _profile(monkeypatch):
    monkeypatch.setattr(
        "open_workflow_agent.services.recovery_profile",
        lambda engine, **kwargs: {
            "profile": "test-only/composition",
            "engine": engine,
            "continuation_supported": True,
            "policy": {},
        },
    )


def test_absent_optional_controls_keep_legacy_descriptors_and_composition(tmp_path, monkeypatch):
    _profile(monkeypatch)
    config = RuntimeConfig()
    assert config.memory.model_dump(mode="json") == {
        "enabled": "auto",
        "database": "/data/memory.sqlite3",
    }
    role = RoleDescriptor.capture("drafter", AgentRoleConfig(id="draft"))
    assert "bounds" not in role.as_value()["model"]
    services = RuntimeServices(config, database_root=tmp_path)
    try:
        assert not isinstance(services.memory, BoundedMemoryService)
        assert services.event_transport is None
        policy = services._recovery_profile("reference")["policy"]
        assert "memory_bounds" not in policy and "event_transport" not in policy
        assert "bounds" not in policy.get("model", {})
    finally:
        services.close()


def test_enabled_bounds_share_actual_invocation_connection_and_pin_nonsecret_policy(
    tmp_path, monkeypatch
):
    _profile(monkeypatch)
    config = RuntimeConfig.model_validate(
        {
            "model": {"bounds": {"id": "provider-policy", "version": "2"}},
            "memory": {"bounds": {"enabled": True, "id": "memory-policy", "version": "2"}},
        }
    )
    services = RuntimeServices(config, database_root=tmp_path, embedded_event_transport=True)
    try:
        assert isinstance(services.memory, BoundedMemoryService)
        assert services.memory._connection is services.invocations.connection
        assert services.event_transport.store is services.event_store
        policy = services._recovery_profile("reference")["policy"]
        assert policy["model"]["bounds"]["id"] == "provider-policy"
        assert policy["memory_bounds"]["id"] == "memory-policy"
        assert policy["event_transport"] == {
            "profile": "owa/embedded-event-transport/v1",
            "id": "embedded",
            "version": "1",
            "max_event_bytes": services.event_store.limits.max_event_bytes,
        }
        assert "password" not in json.dumps(policy)
    finally:
        services.close()


@pytest.mark.parametrize(
    "memory",
    [
        {"database": "foreign.sqlite3", "bounds": {"enabled": True}},
        {"database": "/data/memory.sqlite3", "bounds": {"enabled": True}},
        {"enabled": False, "bounds": {"enabled": True}},
    ],
)
def test_conflicting_legacy_memory_selection_rejects_before_storage(tmp_path, memory):
    with pytest.raises(ConfigurationError):
        RuntimeServices(RuntimeConfig.model_validate({"memory": memory}), database_root=tmp_path)
    assert not (tmp_path / "runtime.sqlite3").exists()


@pytest.mark.parametrize("settings", ["memory-backend", "custom-bus", "nonbool"])
def test_transport_optin_cannot_attach_to_unqualified_bus_before_storage(tmp_path, settings):
    config = (
        RuntimeConfig.model_validate({"events": {"backend": "memory"}})
        if settings == "memory-backend"
        else RuntimeConfig()
    )
    kwargs = (
        {"embedded_event_transport": "enabled"}
        if settings == "nonbool"
        else {"embedded_event_transport": True}
    )
    if settings == "custom-bus":
        kwargs["event_bus"] = object()
    with pytest.raises(ConfigurationError):
        RuntimeServices(config, database_root=tmp_path, **kwargs)
    assert not (tmp_path / "runtime.sqlite3").exists()


@pytest.mark.parametrize(
    "settings",
    [
        {"max_entries": 1001},
        {"max_scan_rows": 10001},
        {"max_query_terms": True},
        {"max_namespace_bytes": 67_108_865},
        {"retention_seconds": float("inf")},
    ],
)
def test_memory_bound_declarations_remain_finite(settings):
    with pytest.raises(ValidationError):
        MemoryBoundsConfig.model_validate(settings)


@pytest.mark.parametrize(
    "payload", [{"query": "a", "limit": True}, {"query": "a", "limit": "1"}, {"query": "a b c"}]
)
def test_role_memory_batch_preflight_uses_configured_strict_bounds_without_sql(
    tmp_path, monkeypatch, payload
):
    services = RuntimeServices(
        RuntimeConfig.model_validate(
            {"memory": {"bounds": {"enabled": True, "max_query_terms": 2}}}
        ),
        database_root=tmp_path,
    )

    def forbidden(*_args):
        pytest.fail("pure role payload preflight queried invocation state")

    monkeypatch.setattr(services.invocations, "get", forbidden)
    try:
        with pytest.raises(WorkflowExecutionError):
            services.validate_agent_tool_payload("search_memory", payload)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_bounded_memory_never_falls_back_to_legacy_anonymous_namespace(tmp_path):
    services = RuntimeServices(
        RuntimeConfig.model_validate({"memory": {"bounds": {"enabled": True}}}),
        database_root=tmp_path,
    )
    try:
        with pytest.raises(ConfigurationError):
            await services.invoke_agent_tool("add_memory", {"text": "must not persist"})
        assert (
            services.memory._connection.execute(
                "SELECT COUNT(*) FROM bounded_memory_entries"
            ).fetchone()[0]
            == 0
        )
    finally:
        services.close()


@pytest.mark.asyncio
async def test_private_retention_requires_its_distinct_permission_before_mutation(
    tmp_path, monkeypatch
):
    services = RuntimeServices(
        RuntimeConfig.model_validate(
            {"memory": {"bounds": {"enabled": True, "retention_seconds": 1}}}
        ),
        database_root=tmp_path,
    )
    observed = []

    async def deny(action, resource):
        observed.append((action, resource))
        raise ToolError("original caller denied private retention")

    def forbidden(**_kwargs):
        pytest.fail("denied maintenance mutated memory")

    monkeypatch.setattr(services, "_memory_namespace", lambda: "original-private-namespace")
    monkeypatch.setattr(services, "authorize_current_action", deny)
    monkeypatch.setattr(services.memory, "prune_due", forbidden)
    try:
        with pytest.raises(ToolError):
            await services._prune_memory()
        assert observed == [("memory.prune", "memory:self")]
    finally:
        services.close()


@pytest.mark.asyncio
async def test_default_and_role_injected_models_cannot_skip_explicit_output_bounds(tmp_path):
    injected, role = FakeModel("x" * 33), FakeModel("x" * 33)
    config = RuntimeConfig.model_validate(
        {
            "model": {"bounds": {"max_output_bytes": 32}},
            "agent": {
                "roles": {"drafter": {"id": "draft", "model": {"bounds": {"max_output_bytes": 32}}}}
            },
        }
    )
    services = RuntimeServices(
        config, model=injected, role_models={"drafter": role}, database_root=tmp_path
    )
    try:
        assert isinstance(services.model, BoundedModel) and services.model.calls is injected.calls
        context = CatalogContext(model=services.model, services=services)
        from open_workflow_agent.errors import ModelError

        with pytest.raises(ModelError):
            await services.catalog.call("llm:1.0.0@default", {}, context)
        with pytest.raises(ModelError):
            await services.catalog.call("llm-drafter:1.0.0@default", {}, context)
        assert len(injected.calls) == len(role.calls) == 1
    finally:
        services.close()
