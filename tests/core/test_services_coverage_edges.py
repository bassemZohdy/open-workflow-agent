"""Deployment-bound service behavior used by independent engine adapters."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from open_workflow_agent.catalog import FakeModel
from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.errors import ConfigurationError
from open_workflow_agent.services import RuntimeServices


def test_engine_database_paths_are_stable_and_namespaced_by_engine(tmp_path) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        assert services.engine_database_path("adk") == str(tmp_path / "adk-sessions.sqlite3")
        assert services.engine_database_path("langgraph") == str(
            tmp_path / "langgraph-checkpoints.sqlite3"
        )
        assert services.engine_database_path("custom") == str(
            tmp_path / "custom-persistence.sqlite3"
        )
    finally:
        services.close()


@pytest.mark.asyncio
async def test_current_action_requires_original_scope_only_when_authorization_is_enabled(
    tmp_path,
) -> None:
    disabled = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path / "off")
    enabled = RuntimeServices(
        RuntimeConfig.model_validate({"execution_authorization": {"enabled": True}}),
        model=FakeModel(),
        database_root=tmp_path / "on",
    )
    try:
        await disabled.authorize_current_action("workflow.run", "run:workflow")
        with pytest.raises(ConfigurationError, match="original invocation scope"):
            await enabled.authorize_current_action("workflow.run", "run:workflow")
    finally:
        disabled.close()
        enabled.close()


@pytest.mark.asyncio
async def test_default_memory_tool_dispatch_preserves_add_search_delete_results(tmp_path) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        created = await services._add_memory(
            {"text": "deployment notes", "metadata": {"source": "workflow"}}, None
        )
        found = await services._search_memory("deployment", None)
        removed = await services._delete_memory({"id": created["id"]}, None)

        assert created["id"] > 0
        assert found[0]["text"] == "deployment notes"
        assert removed == {"deleted": True}
        assert await services._search_memory({"query": "deployment"}, None) == []
    finally:
        services.close()


@pytest.mark.asyncio
async def test_unbounded_memory_maintenance_and_invalid_dispatch_fail_closed(tmp_path) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        with pytest.raises(ConfigurationError, match="maintenance is not enabled"):
            await services._prune_memory()
        with pytest.raises(ConfigurationError, match="not deployment-bound"):
            await services.invoke_agent_tool("unknown_tool", {})
        with pytest.raises(ValueError, match="text string"):
            await services._add_memory({"text": 4}, None)
        with pytest.raises(ValueError, match="metadata must be an object"):
            await services._add_memory({"text": "valid", "metadata": []}, None)
        with pytest.raises(ValueError, match="query and a limit"):
            await services._search_memory({"query": "x", "limit": 0}, None)
        with pytest.raises(ValueError, match="integer id"):
            await services._delete_memory({"id": "1"}, None)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_external_lookup_uses_the_original_invocation_and_transport(tmp_path) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    handle = services.invocations.create(
        engine="test",
        session_id=None,
        user_id=None,
        owner_principal=None,
        workflow_name="lookup",
        workflow_version="1.0.0",
        workflow_fingerprint="a" * 64,
    )
    identity = EffectIdentity(
        handle.invocation_id,
        handle.workflow_fingerprint,
        "/do/0/call",
        (("/do/0/call", 1),),
        "call:http",
    )
    try:
        services.external_action_recovery = SimpleNamespace(http=services.protocols.http)
        await services._authorize_external_lookup(identity, "https://lookup.example/resource", None)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_external_lookup_and_role_inputs_fail_closed_without_bindings(tmp_path) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    missing = EffectIdentity(
        "missing-invocation",
        "b" * 64,
        "/do/0/call",
        (("/do/0/call", 1),),
        "call:http",
    )
    try:
        with pytest.raises(ConfigurationError, match="original invocation"):
            await services._authorize_external_lookup(
                missing, "https://lookup.example/resource", None
            )
        with pytest.raises(ConfigurationError, match="not deployment-bound"):
            await services.authorize_protocol_destination("mcp", "stdio:missing", {})
        with pytest.raises(ConfigurationError, match="cannot choose its namespace"):
            services.validate_agent_tool_payload(
                "add_memory", {"text": "request", "namespace": "another-user"}
            )
        with pytest.raises(ConfigurationError, match="search tool input is invalid"):
            services.validate_agent_tool_payload("search_memory", {"query": "x", "limit": True})
    finally:
        services.close()


def test_engine_database_path_supports_external_and_default_datasources(tmp_path) -> None:
    services = RuntimeServices(RuntimeConfig(), model=FakeModel(), database_root=tmp_path)
    try:
        services.database_root = None
        services.datasource = "postgresql://runtime:secret@db.example/workflows"
        external = services.engine_database_path("custom", create_namespace=False)
        assert "owa_custom" in external

        services.datasource = None
        local = services.engine_database_path("adk")
        assert Path(local).name == "adk-sessions.sqlite3"
    finally:
        services.close()
