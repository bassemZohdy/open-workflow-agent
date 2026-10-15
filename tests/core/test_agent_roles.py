"""Finite role descriptors, common model routing and immutable deployment pins."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest
from open_workflow_agent.agent_roles import RoleDescriptor, current_role_descriptor
from open_workflow_agent.catalog import CatalogContext, FakeModel
from open_workflow_agent.config import AgentRoleConfig, RuntimeConfig, ToolConfig
from open_workflow_agent.errors import ConfigurationError, ModelError, ToolError
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow
from pydantic import ValidationError


def _config(**changes):
    return RuntimeConfig.model_validate(
        {
            "agent": {
                "roles": {
                    "drafter": {
                        "id": "draft-v1",
                        "instruction": "Draft the answer.",
                        "model": {"name": "fake/draft"},
                        **changes,
                    },
                    "reviewer": {
                        "id": "review-v1",
                        "instruction": "Review independently.",
                        "model": {"name": "fake/review"},
                    },
                }
            }
        }
    )


def _context(services):
    return CatalogContext(model=services.model, services=services)


@pytest.mark.asyncio
async def test_distinct_role_models_instructions_and_common_catalog_references(tmp_path):
    default, draft, review = (
        FakeModel("default"),
        FakeModel({"draft": True}),
        FakeModel({"approved": True}),
    )
    services = RuntimeServices(
        _config(),
        model=default,
        role_models={"drafter": draft, "reviewer": review},
        database_root=tmp_path,
    )
    try:
        assert services.role_function_calls == frozenset(
            {
                "agent-drafter:1.0.0@default",
                "llm-drafter:1.0.0@default",
                "agent-reviewer:1.0.0@default",
                "llm-reviewer:1.0.0@default",
            }
        )
        assert await services.catalog.call(
            "agent-drafter:1.0.0@default", {"input": "original"}, _context(services)
        ) == {"draft": True}
        assert await services.catalog.call(
            "llm-reviewer:1.0.0@default", {"input": "original"}, _context(services)
        ) == {"approved": True}
        assert draft.calls[0]["instruction"] == "Draft the answer."
        assert review.calls[0]["instruction"] == "Review independently."
        assert draft.calls[0]["tools"] == review.calls[0]["tools"] == []
        assert default.calls == [] and current_role_descriptor() is None
    finally:
        services.close()


@pytest.mark.asyncio
async def test_empty_roles_preserve_default_generated_agent_and_llm(tmp_path):
    default = FakeModel(["agent", "llm"])
    services = RuntimeServices(RuntimeConfig(), model=default, database_root=tmp_path)
    try:
        assert services.role_function_calls == frozenset()
        assert generate_default_workflow()["do"][0]["respond"]["call"] == "agent:1.0.0@default"
        assert (
            await services.catalog.call("agent:1.0.0@default", "original", _context(services))
            == "agent"
        )
        assert (
            await services.catalog.call("llm:1.0.0@default", "original", _context(services))
            == "llm"
        )
        assert len(default.calls) == 2
    finally:
        services.close()


@pytest.mark.parametrize(
    "change",
    [
        {"max_rounds": True},
        {"max_model_calls": 0},
        {"max_tool_calls": 65},
        {"max_child_invocations": 1},
        {"max_output_bytes": 4_194_305},
        {"version": "latest"},
        {"tools": ["same", "same"]},
        {"model": {"options": {"api_key": "RAW_CANARY"}}},
        {"model": {"credentials": {"api_key": "RAW_CANARY"}}},
        {"model": {"options": {"unclassified": {"secret": "RAW_CANARY"}}}},
        {"model": {"options": {"max_tokens": True}}},
        {"output_schema": {"$ref": "https://untrusted.test/schema"}},
    ],
    ids=[
        "bool-rounds",
        "zero-models",
        "tool-limit",
        "delegation",
        "output-limit",
        "version",
        "duplicate-tools",
        "raw-option",
        "raw-credential",
        "opaque-option",
        "bool-tokens",
        "external-schema",
    ],
)
def test_unqualified_role_declarations_reject_before_runtime_creation(change):
    with pytest.raises(ValidationError) as caught:
        AgentRoleConfig.model_validate({"id": "role-v1", **change})
    assert "RAW_CANARY" not in str(caught.value)


def test_descriptor_is_immutable_json_and_contains_only_credential_references():
    config = AgentRoleConfig.model_validate(
        {
            "id": "role-v1",
            "model": {
                "name": "provider/model",
                "options": {"max_tokens": 128},
                "credentials": {"api_key": {"from_env": "OWA_ROLE_KEY"}},
            },
        }
    )
    descriptor = RoleDescriptor.capture("drafter", config)
    value = descriptor.as_value()
    assert value["model"]["credentials"] == {"api_key": {"from_env": "OWA_ROLE_KEY"}}
    original = descriptor.digest
    value["tools"].append("foreign")
    assert descriptor.as_value()["tools"] == [] and descriptor.digest == original
    config.instruction = "changed"
    assert RoleDescriptor.capture("drafter", config).digest != original


@pytest.mark.parametrize("change", ["instruction", "model", "version", "allowlist", "options"])
@pytest.mark.asyncio
async def test_current_binding_or_actual_tool_allowlist_changes_reject_before_role_model(
    tmp_path, change
):
    model = FakeModel("not-called")
    config = _config(tools=["search_knowledge"])
    services = RuntimeServices(config, role_models={"drafter": model}, database_root=tmp_path)
    try:
        original = services.role_bindings.snapshot()
        if change == "instruction":
            config.agent.roles["drafter"].instruction = "new"
        elif change == "model":
            config.agent.roles["drafter"].model.name = "fake/new"
        elif change == "version":
            config.agent.roles["drafter"].version = "2.0.0"
        elif change == "allowlist":
            services.agent_tools = tuple(
                name for name in services.agent_tools if name != "search_knowledge"
            )
        else:
            config.agent.roles["drafter"].model.options["api_key"] = "RAW_CANARY"
        with pytest.raises((ConfigurationError, ValidationError)):
            await services.catalog.call("agent-drafter:1.0.0@default", {}, _context(services))
        assert model.calls == [] and original["bindings"][0]["id"] == "draft-v1"
    finally:
        services.close()


@pytest.mark.parametrize(
    "response,schema,budget",
    [
        (
            {"ok": True},
            {"type": "object", "properties": {"ok": {"const": True}}, "required": ["ok"]},
            64,
        ),
        (
            {"ok": False},
            {"type": "object", "properties": {"ok": {"const": True}}, "required": ["ok"]},
            64,
        ),
        ("x" * 65, None, 64),
    ],
    ids=["valid-schema", "invalid-schema", "oversize"],
)
@pytest.mark.asyncio
async def test_final_model_output_obeys_declared_schema_and_byte_budget(
    tmp_path, response, schema, budget
):
    model = FakeModel(response)
    services = RuntimeServices(
        _config(output_schema=schema, max_output_bytes=budget),
        role_models={"drafter": model},
        database_root=tmp_path,
    )
    try:
        if response == {"ok": True}:
            assert (
                await services.catalog.call("llm-drafter:1.0.0@default", {}, _context(services))
                == response
            )
        else:
            with pytest.raises(ModelError):
                await services.catalog.call("llm-drafter:1.0.0@default", {}, _context(services))
        assert len(model.calls) == 1 and current_role_descriptor() is None
    finally:
        services.close()


@pytest.mark.parametrize("name", ["missing", "drafter-extra"])
def test_injected_model_cannot_create_undeclared_role_before_storage(tmp_path, name):
    with pytest.raises(ConfigurationError):
        RuntimeServices(_config(), role_models={name: FakeModel()}, database_root=tmp_path)
    assert not (tmp_path / "runtime.sqlite3").exists()


@pytest.mark.asyncio
async def test_direct_llm_role_never_executes_model_generated_tools(tmp_path, monkeypatch):
    model = FakeModel({"tool_call": {"name": "search_knowledge", "arguments": {}}})
    services = RuntimeServices(
        _config(tools=["search_knowledge"]), role_models={"drafter": model}, database_root=tmp_path
    )

    def forbidden(*_args):
        pytest.fail("direct LLM role dispatched a tool")

    monkeypatch.setattr(services.knowledge, "search", forbidden)
    try:
        with pytest.raises(ToolError):
            await services.catalog.call("llm-drafter:1.0.0@default", {}, _context(services))
        assert len(model.calls) == 1
    finally:
        services.close()


def test_role_manifest_records_actual_allowlist_without_revealing_secret_values(
    tmp_path, monkeypatch
):
    import open_workflow_agent.services as module

    monkeypatch.setattr(
        module,
        "recovery_profile",
        lambda *args, **kwargs: {"policy": {"model": {}}, "continuation_supported": True},
    )
    services = RuntimeServices(_config(), database_root=tmp_path)
    try:
        profile = services._recovery_profile("reference")
        assert profile["policy"]["agent_roles"] == services.role_bindings.snapshot()
        assert "RAW_CANARY" not in json.dumps(profile)
        empty = RuntimeServices(RuntimeConfig(), database_root=tmp_path / "empty")
        try:
            assert "agent_roles" not in empty._recovery_profile("reference")["policy"]
        finally:
            empty.close()
    finally:
        services.close()


def test_role_references_use_existing_catalog_grammar_and_exact_admission():
    source = {
        "document": {"dsl": "1.0.3", "namespace": "roles", "name": "selected", "version": "1.0.0"},
        "do": [{"draft": {"call": "agent-drafter:1.0.0@default", "with": {"input": "original"}}}],
    }
    configured = frozenset({"agent-drafter:1.0.0@default"})
    assert compile_workflow(source, enabled_function_calls=configured).source == source
    from open_workflow_agent.errors import UnsupportedWorkflowFeature

    with pytest.raises(UnsupportedWorkflowFeature):
        compile_workflow(source)
    changed = copy.deepcopy(source)
    changed["do"][0]["draft"]["call"] = "agent-drafter:2.0.0@default"
    with pytest.raises(UnsupportedWorkflowFeature):
        compile_workflow(changed, enabled_function_calls=configured)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["endpoint", "method", "remote-name"])
async def test_actual_role_tool_route_change_rejects_before_model_or_transport(tmp_path, changed):
    config = _config(tools=["peer"])
    config.tools = [
        ToolConfig.model_validate(
            {
                "type": "mcp",
                "name": "peer",
                "endpoint": "https://safe.test/mcp",
                "options": {"method": "tools/call", "name": "original"},
            }
        )
    ]
    model = FakeModel("unused")
    services = RuntimeServices(config, role_models={"drafter": model}, database_root=tmp_path)
    try:
        saved = services.role_bindings.snapshot()
        assert saved["effective_tool_bindings"][0]["endpoint"] == "https://safe.test/mcp"
        tool = services.tools.tools["peer"]
        if changed == "endpoint":
            services.tools.tools["peer"] = replace(tool, endpoint="https://foreign.test/mcp")
        else:
            tool.options["method" if changed == "method" else "name"] = (
                "tools/list" if changed == "method" else "foreign"
            )
        with pytest.raises(ConfigurationError, match="routing changed"):
            await services.catalog.call("agent-drafter:1.0.0@default", {}, _context(services))
        assert model.calls == []
        assert saved["effective_tool_bindings"][0]["options"]["name"] == "original"
    finally:
        services.close()


def test_unqualified_role_tool_options_reject_before_storage_without_credential_echo(tmp_path):
    config = _config(tools=["peer"])
    config.tools = [
        ToolConfig.model_validate(
            {
                "type": "mcp",
                "name": "peer",
                "endpoint": "https://safe.test/mcp",
                "options": {"headers": {"Authorization": "RAW_CANARY"}},
            }
        )
    ]
    with pytest.raises(ConfigurationError) as caught:
        RuntimeServices(config, database_root=tmp_path)
    assert "RAW_CANARY" not in str(caught.value)
    assert not (tmp_path / "runtime.sqlite3").exists()


@pytest.mark.asyncio
async def test_ambiguous_role_tool_argument_forms_reject_before_any_tool(tmp_path, monkeypatch):
    model = FakeModel(
        {
            "tool_calls": [
                {"name": "search_knowledge", "arguments": {"query": "a"}, "input": {"query": "b"}},
            ]
        }
    )
    services = RuntimeServices(
        _config(tools=["search_knowledge"]), role_models={"drafter": model}, database_root=tmp_path
    )

    def forbidden(*_args):
        pytest.fail("ambiguous role request reached a tool")

    monkeypatch.setattr(services.knowledge, "search", forbidden)
    try:
        with pytest.raises(ToolError):
            await services.catalog.call("agent-drafter:1.0.0@default", {}, _context(services))
        assert len(model.calls) == 1 and current_role_descriptor() is None
    finally:
        services.close()
