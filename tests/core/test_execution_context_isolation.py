"""Scoped identity/action controls do not escape into managed catalog metadata."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from open_workflow_agent.catalog import CatalogContext, FakeModel
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.execution_authority import (
    ExecutionAuthority,
    authority_scope,
    current_authority,
)
from open_workflow_agent.execution_context import (
    ExecutionActionContext,
    action_scope,
    current_action_context,
)
from open_workflow_agent.persistence import ExecutionHandle
from open_workflow_agent.security import Principal


def action(identity):
    invocation = ExecutionHandle(
        invocation_id=identity,
        engine="portable",
        engine_execution_reference="private-" + identity,
        user_id="same-user",
        session_id="same-session",
        workflow_fingerprint="0" * 64,
        workflow_name="fixture",
        workflow_version="1",
        owner_principal=identity,
    )
    effect = EffectIdentity(
        identity, "0" * 64, "/do/0/action", (("/do/0/action", 1),), "call:fixture"
    )
    return ExecutionActionContext(invocation, effect)


@pytest.mark.asyncio
async def test_managed_context_excludes_controls_and_deeply_freezes_public_authority():
    model = FakeModel(response="safe")
    calls = []

    async def authorize_model():
        calls.append("model")

    async def tool(name, payload):
        calls.append((name, payload))
        return "tool"

    authority = ExecutionAuthority.capture(
        Principal("original", frozenset({"reader"})), security_profile="actor"
    ).as_value()
    services = SimpleNamespace(
        agent_tools=("approved",),
        invoke_agent_tool=tool,
        authorize_model_completion=authorize_model,
        config=object(),
        effect_journal=object(),
        sandbox=object(),
    )
    context = CatalogContext(
        model,
        services=services,
        metadata={
            "invocation_id": "original",
            "authority": authority,
            "task_reference": "/do/0/action",
            "_invocation_handle": object(),
            "_cancel_child_scopes": lambda: None,
            "_lifecycle": object(),
            "_workflow_source": {"private": True},
            "engine_execution_reference": "private-native-key",
        },
    )
    assert set(context.metadata) == {"invocation_id", "authority", "task_reference"}
    assert not hasattr(context.services, "config") and not hasattr(context.services, "sandbox")
    with pytest.raises(TypeError):
        context.metadata["invocation_id"] = "replacement"
    with pytest.raises(TypeError):
        context.metadata["authority"]["principal"]["identity"] = "replacement"
    authority["principal"]["roles"].append("admin")
    assert context.metadata["authority"]["principal"]["roles"] == ("reader",)
    assert await context.model.complete("prompt") == "safe"
    assert await context.services.invoke_agent_tool("approved", {"input": None}) == "tool"
    assert calls == ["model", ("approved", {"input": None})] and model.calls == ["prompt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["fault", "cancel"])
async def test_concurrent_task_local_authority_and_action_reset_after_interruption(interruption):
    ready = [asyncio.Event(), asyncio.Event()]
    proceed = asyncio.Event()
    observed = []

    async def run(index):
        principal = Principal("principal-" + str(index), frozenset({"role-" + str(index)}))
        authority = ExecutionAuthority.capture(principal, security_profile="actor-" + str(index))
        scope = action(principal.identity)
        try:
            with authority_scope(authority), action_scope(scope):
                ready[index].set()
                await proceed.wait()
                observed.append((current_authority().principal, current_action_context().identity))
                if index == 0:
                    if interruption == "cancel":
                        raise asyncio.CancelledError
                    raise ValueError("controlled")
        finally:
            assert current_authority() is None and current_action_context() is None

    tasks = [asyncio.create_task(run(index)) for index in range(2)]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready)), 5)
        assert current_authority() is None and current_action_context() is None
        proceed.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        assert isinstance(
            results[0], asyncio.CancelledError if interruption == "cancel" else ValueError
        )
        assert results[1] is None
        assert {(principal.identity, effect.invocation_id) for principal, effect in observed} == {
            ("principal-0", "principal-0"),
            ("principal-1", "principal-1"),
        }
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
