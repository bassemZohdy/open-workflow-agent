"""Controlled provider DTO/SPI proofs; no actual vendor or native qualification."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from open_workflow_agent.catalog import (
    BoundedModel,
    CatalogContext,
    FakeModel,
    FunctionCatalog,
    LiteLLMModel,
)
from open_workflow_agent.config import ModelConfig, ProviderBoundsConfig, RuntimeConfig
from open_workflow_agent.effects import EffectIdentity
from open_workflow_agent.engine import PortableWorkflowEngine
from open_workflow_agent.errors import ModelError, OwaError, ToolError, _ModelOutcomeUncertain
from open_workflow_agent.execution_authority import ExecutionAuthority, authority_scope
from open_workflow_agent.model_responses import ModelToolRequest, completion_message
from open_workflow_agent.recovery_definitions import PROFILE
from open_workflow_agent.security import SecretReference, static_principal
from open_workflow_agent.services import RuntimeServices
from open_workflow_agent.timer_dispatcher import TimerDispatcher
from open_workflow_agent.workflow import compile_workflow
from pydantic import ValidationError

pytestmark = pytest.mark.usefixtures("explicit_workflow_delivery")


@pytest.fixture
def explicit_workflow_delivery(monkeypatch):
    """Unit receipts use explicit invokes, without claiming automatic delivery."""

    async def no_automatic_delivery(_dispatcher):
        return None

    monkeypatch.setattr(TimerDispatcher, "start", no_automatic_delivery)


def _reply(content=None, *, calls=None, legacy=None, **message):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    role="assistant",
                    content=content,
                    tool_calls=calls,
                    function_call=legacy,
                    **message,
                )
            )
        ]
    )


def _call(
    name="search_knowledge",
    arguments='{"query":"original","nullable":null}',
    identifier="provider-id",
):
    return SimpleNamespace(
        id=identifier, type="function", function=SimpleNamespace(name=name, arguments=arguments)
    )


def _project(response, **limits):
    return completion_message(
        response,
        max_output_bytes=limits.get("output", 4096),
        max_tool_calls=limits.get("tools", 4),
        max_argument_bytes=limits.get("arguments", 1024),
    )


@pytest.fixture
def provider(monkeypatch):
    calls = []
    replies = []

    async def complete(**kwargs):
        calls.append(kwargs)
        result = replies.pop(0)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            result = result(kwargs)
        if hasattr(result, "__await__"):
            return await result
        return result

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=complete))
    return calls, replies


@pytest.mark.parametrize("content", ["old text outcome", None])
@pytest.mark.asyncio
async def test_provider_text_and_null_keep_old_shape_and_have_no_transparent_retry(
    provider, content
):
    calls, replies = provider
    replies.append(_reply(content))
    result = await LiteLLMModel("provider/unit").complete({"input": "original"})
    assert result == content and len(calls) == 1
    assert calls[0]["num_retries"] == calls[0]["max_retries"] == 0
    assert 0 < calls[0]["timeout"] <= 30


@pytest.mark.parametrize("legacy", [False, True])
def test_provider_function_requests_remain_ordered_finite_neutral_dtos(legacy):
    first = _call()
    response = (
        _reply("retained text", legacy=first.function)
        if legacy
        else _reply(
            "retained text", calls=[first, _call("add_memory", '{"text":"second"}', "second-id")]
        )
    )
    result = _project(response)
    assert result["content"] == "retained text"
    assert result["tool_calls"][0] == {
        "name": "search_knowledge",
        "arguments": {"query": "original", "nullable": None},
    }
    assert "provider-id" not in json.dumps(result)
    if not legacy:
        assert [request["name"] for request in result["tool_calls"]] == [
            "search_knowledge",
            "add_memory",
        ]


@pytest.mark.parametrize(
    "arguments", ['{"x":1,"x":2}', '{"x":NaN}', "[1]", "null", "{", '{"x":' + "9" * 400 + "}"]
)
def test_malformed_or_nonobject_tool_arguments_cannot_reach_a_tool(arguments):
    with pytest.raises(ModelError):
        _project(_reply(calls=[_call(arguments=arguments)]))


@pytest.mark.parametrize(
    "damage",
    [
        "choices",
        "role",
        "content",
        "refusal",
        "type",
        "name",
        "duplicate-id",
        "ambiguous",
        "count",
        "argument-size",
        "output-size",
    ],
)
def test_unsupported_or_oversized_provider_message_rejects_without_body_echo(damage):
    response = _reply("RAW_CANARY", calls=[_call()])
    limits = {}
    message = response.choices[0].message
    if damage == "choices":
        response.choices.append(response.choices[0])
    elif damage == "role":
        message.role = "tool"
    elif damage == "content":
        message.content = [{"type": "image"}]
    elif damage == "refusal":
        message.refusal = "RAW_CANARY"
    elif damage == "type":
        message.tool_calls[0].type = "custom"
    elif damage == "name":
        message.tool_calls[0].function.name = "foreign\nheader"
    elif damage == "duplicate-id":
        message.tool_calls.append(copy.deepcopy(message.tool_calls[0]))
    elif damage == "ambiguous":
        message.function_call = message.tool_calls[0].function
    elif damage == "count":
        limits["tools"] = 0
    elif damage == "argument-size":
        limits["arguments"] = 8
    elif damage == "output-size":
        limits["output"] = 4
    with pytest.raises(ModelError) as caught:
        _project(response, **limits)
    assert "RAW_CANARY" not in str(caught.value) and "RAW_CANARY" not in json.dumps(
        caught.value.as_dict()
    )


def test_neutral_request_owns_immutable_finite_argument_copy():
    arguments = {"original": None}
    request = ModelToolRequest.capture("safe", arguments, max_argument_bytes=64)
    arguments["foreign"] = True
    view = request.as_value()
    view["arguments"]["foreign"] = True
    assert request.as_value() == {"name": "safe", "arguments": {"original": None}}


@pytest.mark.parametrize(
    "options",
    [
        {"stream": True},
        {"num_retries": 1},
        {"max_retries": 1},
        {"fallbacks": []},
        {"model": "foreign"},
        {"api_key": "RAW_CANARY"},
        {"n": 2},
        {"timeout": float("inf")},
    ],
)
@pytest.mark.asyncio
async def test_local_unsupported_behavior_rejects_before_secret_read_or_provider_attempt(
    provider, monkeypatch, options
):
    calls, _ = provider

    def forbidden(_reference):
        pytest.fail("invalid provider request resolved a credential")

    monkeypatch.setattr("open_workflow_agent.catalog.resolve_secret", forbidden)
    model = LiteLLMModel(
        "provider/unit", credentials={"api_key": SecretReference(from_env="OWA_UNIT_KEY")}
    )
    with pytest.raises(ModelError) as caught:
        await model.complete("original", options=options)
    assert calls == [] and "RAW_CANARY" not in str(caught.value)


@pytest.mark.asyncio
async def test_original_credential_denial_is_known_before_any_resolution_or_attempt(
    provider, monkeypatch
):
    calls, _ = provider

    async def deny(reference):
        assert reference == "OWA_UNIT_KEY"
        raise ToolError("original caller denied credential")

    def forbidden(_reference):
        pytest.fail("denied provider resolved a credential")

    monkeypatch.setattr("open_workflow_agent.catalog.resolve_secret", forbidden)
    model = LiteLLMModel(
        "provider/unit",
        credentials={"api_key": SecretReference(from_env="OWA_UNIT_KEY")},
        authorize_credential=deny,
    )
    with pytest.raises(ToolError):
        await model.complete("original")
    assert calls == []


@pytest.mark.asyncio
async def test_selected_credential_echo_is_projected_in_text_and_tool_arguments(
    provider, monkeypatch
):
    calls, replies = provider
    monkeypatch.setenv("OWA_UNIT_KEY", "controlled-provider-secret")
    replies.extend(
        [
            _reply("echo controlled-provider-secret"),
            _reply(calls=[_call(arguments='{"query":"controlled-provider-secret"}')]),
        ]
    )
    model = LiteLLMModel(
        "provider/unit", credentials={"api_key": SecretReference(from_env="OWA_UNIT_KEY")}
    )
    outputs = [await model.complete("original"), await model.complete("original")]
    assert "controlled-provider-secret" not in json.dumps(outputs)
    assert len(calls) == 2 and all(
        call["api_key"] == "controlled-provider-secret" for call in calls
    )
    assert outputs[1]["tool_calls"][0]["arguments"]["query"] != "controlled-provider-secret"


@pytest.mark.asyncio
async def test_provider_prepare_and_attempt_share_original_deadline_and_cancel_pending_io(
    provider, monkeypatch
):
    calls, replies = provider
    stopped = asyncio.Event()

    async def authorize(_reference):
        await asyncio.sleep(0.04)

    async def wait(_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    replies.append(wait)
    model = LiteLLMModel(
        "provider/unit",
        bounds=ProviderBoundsConfig(timeout_seconds=0.08),
        credentials={"api_key": SecretReference(from_env="OWA_UNIT_KEY")},
        authorize_credential=authorize,
    )
    # Use the actual resolver with a controlled deployment reference.
    monkeypatch.setenv("OWA_UNIT_KEY", "controlled-provider-secret")
    with pytest.raises(_ModelOutcomeUncertain) as caught:
        await model.complete("original")
    assert caught.value.details["reason"] == "timeout"
    assert len(calls) == 1 and calls[0]["timeout"] < 0.06 and stopped.is_set()


@pytest.mark.asyncio
async def test_preparation_timeout_is_known_with_zero_provider_attempts(provider):
    calls, _ = provider

    async def blocked(_reference):
        await asyncio.Event().wait()

    model = LiteLLMModel(
        "provider/unit",
        bounds=ProviderBoundsConfig(timeout_seconds=0.04),
        credentials={"api_key": SecretReference(from_env="OWA_UNIT_KEY")},
        authorize_credential=blocked,
    )
    with pytest.raises(ModelError) as caught:
        await model.complete("original")
    assert not isinstance(caught.value, _ModelOutcomeUncertain) and calls == []


@pytest.mark.asyncio
async def test_host_cancellation_propagates_after_provider_cleanup(provider):
    calls, replies = provider
    started, stopped = asyncio.Event(), asyncio.Event()

    async def wait(_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    replies.append(wait)
    task = asyncio.create_task(LiteLLMModel("provider/unit").complete("original"))
    async with asyncio.timeout(1):
        await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1 and stopped.is_set()


@pytest.mark.asyncio
async def test_default_agent_executes_provider_dto_but_direct_llm_only_returns_it(provider):
    calls, replies = provider
    replies.extend([_reply(calls=[_call()]), _reply("known final text"), _reply(calls=[_call()])])
    model = LiteLLMModel("provider/unit")
    tools = []

    class Facade:
        agent_tools = ("search_knowledge",)

        async def invoke_agent_tool(self, name, arguments):
            tools.append((name, arguments))
            return {"result": None}

    facade = Facade()
    catalog = FunctionCatalog.default(model)
    context = CatalogContext(model=model, services=facade)
    assert (
        await catalog.call("agent:1.0.0@default", {"input": "original"}, context)
        == "known final text"
    )
    direct = await catalog.call("llm:1.0.0@default", {"input": "original"}, context)
    assert direct["tool_calls"][0]["name"] == "search_knowledge"
    assert len(calls) == 3 and tools == [
        ("search_knowledge", {"query": "original", "nullable": None})
    ]


def test_absent_provider_bounds_preserve_previous_model_declaration_shape():
    assert ModelConfig().model_dump(mode="json") == {
        "provider": "fake",
        "name": "fake/default",
        "temperature": 0.0,
        "options": {},
        "credentials": {},
    }
    assert (
        ModelConfig(bounds=ProviderBoundsConfig(id="pinned", version="2")).model_dump(mode="json")[
            "bounds"
        ]["id"]
        == "pinned"
    )


@pytest.mark.parametrize(
    "bounds",
    [
        {"timeout_seconds": True},
        {"timeout_seconds": float("nan")},
        {"max_tool_calls": True},
        {"max_output_bytes": 16_777_217},
        {"id": "untrusted\nref"},
    ],
)
def test_provider_bounds_reject_unqualified_declarations(bounds):
    with pytest.raises(ValidationError):
        ProviderBoundsConfig.model_validate(bounds)


@pytest.fixture(params=["sqlite", "postgresql"])
async def provider_runtime(request, tmp_path, monkeypatch, provider):
    backend = request.param
    datasource = os.getenv("OWA_TEST_POSTGRES_URL") if backend == "postgresql" else None
    if backend == "postgresql" and not datasource:
        pytest.skip("actual PostgreSQL is required for common provider receipt proof")
    marker = uuid4().hex
    monkeypatch.setenv("OWA_PROVIDER_CALLER", "controlled-caller-token")
    config = RuntimeConfig.model_validate(
        {
            "model": {
                "provider": "litellm",
                "name": "provider/unit",
                "bounds": {"timeout_seconds": 0.08},
            },
            "persistence": {"datasource": datasource},
            "security": {
                "profiles": {
                    "caller": {
                        "type": "bearer",
                        "principal": "caller-" + marker,
                        "token": {"from_env": "OWA_PROVIDER_CALLER"},
                    }
                }
            },
            "execution_authorization": {
                "enabled": True,
                "policy": {"rules": [{"actions": ["*"], "resources": ["*"]}]},
            },
        }
    )
    services = RuntimeServices(config, database_root=None if datasource else tmp_path / marker)
    assert (
        services.invocations.connection.kind == services.effect_journal.connection.kind == backend
    )
    services.recovery_definitions.profile_provider = lambda engine: {
        "profile": PROFILE,
        "engine": engine,
        "backend": backend,
        "continuation_supported": True,
        "qualification": "test-only/common-provider-receipts",
        "policy": {
            "execution_authorization": config.execution_authorization.model_dump(mode="json"),
            "model": config.model.model_dump(mode="json"),
        },
    }
    engine = PortableWorkflowEngine()
    await engine.initialize(services)
    authority = ExecutionAuthority.capture(
        static_principal(config.security.profile("caller")), security_profile="caller"
    )
    try:
        yield services, engine, authority, provider
    finally:
        await engine.shutdown()
        services.close()


async def _invoke(runtime, *, source=None):
    services, engine, authority, _ = runtime
    plan = compile_workflow(
        source
        or {
            "document": {
                "dsl": "1.0.3",
                "namespace": "provider-spi",
                "name": "case-" + uuid4().hex,
                "version": "1.0.0",
            },
            "do": [{"generate": {"call": "llm:1.0.0@default", "with": {"input": "original"}}}],
        }
    )
    handle = services.invocations.create(
        engine="reference",
        session_id=None,
        user_id="untrusted-label",
        owner_principal=authority.principal.identity,
        workflow_name=plan.name,
        workflow_version=plan.version,
        workflow_fingerprint=plan.fingerprint,
    )
    services.execution_authorizations.reserve_invocation(
        handle, authority, input_data={"original": None}
    )
    with authority_scope(authority):
        async with asyncio.timeout(3):
            result = await engine.invoke(plan, handle, {"original": None})
    return result, handle, plan


@pytest.mark.parametrize("content", ["known text", None])
@pytest.mark.asyncio
async def test_known_provider_receipt_replay_never_calls_provider_again(provider_runtime, content):
    services, engine, _, (calls, replies) = provider_runtime
    replies.append(_reply(content))
    first, handle, plan = await _invoke(provider_runtime)
    assert first.status == "completed" and first.output == content
    async with asyncio.timeout(3):
        duplicate = await engine.resume(handle, {}, plan)
    assert duplicate.output == content and len(calls) == 1
    assert services.execution_authorizations.verify_invocation(handle) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["transport", "invalid-response", "oversize"])
async def test_provider_transport_unknown_retains_original_pending_catalog_intent(
    provider_runtime, outcome
):
    services, _, _, (calls, replies) = provider_runtime
    replies.append(
        OSError("RAW_CANARY transport outcome unknown")
        if outcome == "transport"
        else _reply(calls=[_call(arguments="[]")])
        if outcome == "invalid-response"
        else _reply("x" * 1_048_577)
    )
    source = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "provider-spi",
            "name": "unknown-" + uuid4().hex,
            "version": "1.0.0",
        },
        "do": [
            {
                "guarded": {
                    "try": [
                        {"generate": {"call": "llm:1.0.0@default", "with": {"input": "original"}}}
                    ],
                    "catch": {
                        "errors": {"with": {"type": "${ true }"}},
                        "retry": {"limit": {"attempt": {"count": 2}}},
                        "do": [{"mustNotRun": {"set": {"fabricated": True}}}],
                    },
                }
            }
        ],
    }
    result, handle, plan = await _invoke(provider_runtime, source=source)
    assert result.status != "completed" and len(calls) == 1
    rows = services.effect_journal.connection.execute(
        "SELECT payload FROM workflow_effect_invocations WHERE invocation_id=? AND fingerprint=?",
        (handle.invocation_id, plan.fingerprint),
    ).fetchall()
    entries = [EffectIdentity.from_value(json.loads(row[0])) for row in rows]
    business = [identity for identity in entries if identity.operation == "call:llm:1.0.0@default"]
    assert len(business) == 1
    receipt = services.effect_journal.get(business[0])
    assert receipt is not None and receipt.status == "pending"
    assert "RAW_CANARY" not in json.dumps(result.as_dict())
    assert result.output != {"fabricated": True}


@pytest.mark.asyncio
async def test_changed_provider_bounds_reject_original_archive_before_any_new_ownership(
    provider_runtime,
):
    services, _, _, (calls, replies) = provider_runtime
    replies.append(_reply("known"))
    _, handle, plan = await _invoke(provider_runtime)
    before = services.invocations.get(handle.invocation_id).as_dict()
    services.config.model.bounds.max_output_bytes = 2048
    with pytest.raises(OwaError) as caught:
        services.recovery_definitions.verify(handle, plan)
    assert caught.value.details["reason"] == "runtime_profile_changed"
    assert services.invocations.get(handle.invocation_id).as_dict() == before
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_existing_default_agent_input_alias_is_preserved():
    received = []

    class Facade:
        agent_tools = ("search_knowledge",)

        async def invoke_agent_tool(self, name, payload):
            received.append((name, payload))
            return None

    model = FakeModel(
        [{"tool_call": {"name": "search_knowledge", "input": "legacy shorthand"}}, "known final"]
    )
    result = await FunctionCatalog.default(model).call(
        "agent:1.0.0@default", {}, CatalogContext(model=model, services=Facade())
    )
    assert result == "known final" and received == [("search_knowledge", "legacy shorthand")]


@pytest.mark.parametrize("tag", ["anthropic-direct", "bedrock-direct", "unknown"])
def test_unsupported_provider_registry_tags_reject_at_configuration(tag):
    with pytest.raises(ValidationError):
        ModelConfig(provider=tag)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["input", "output", "tool-count", "argument"])
async def test_optin_common_model_bounds_enforce_fake_and_injected_spi(direction):
    response = "known"
    prompt = "original"
    limits = {
        "max_input_bytes": 128,
        "max_output_bytes": 256,
        "max_tool_calls": 1,
        "max_argument_bytes": 32,
    }
    if direction == "input":
        prompt = "x" * 129
    elif direction == "output":
        response = "x" * 257
    elif direction == "tool-count":
        response = {
            "tool_calls": [{"name": "safe", "arguments": {}}, {"name": "safe", "arguments": {}}]
        }
    else:
        response = {"tool_call": {"name": "safe", "input": "x" * 33}}
    injected = FakeModel(response)
    guarded = BoundedModel(injected, ProviderBoundsConfig(**limits))
    with pytest.raises(ModelError):
        await guarded.complete(prompt)
    assert guarded.calls is injected.calls
    assert len(injected.calls) == (0 if direction == "input" else 1)


@pytest.mark.asyncio
async def test_explicit_bounds_preserve_common_alias_and_counter_observability():
    injected = FakeModel({"tool_call": {"name": "safe", "input": {"legacy": "shorthand"}}})
    guarded = BoundedModel(injected, ProviderBoundsConfig())
    result = await guarded.complete({"original": None})
    assert result == {"tool_call": {"name": "safe", "input": {"legacy": "shorthand"}}}
    assert guarded.calls is injected.calls and guarded.calls == [{"original": None}]


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", ["opaque", [1], None])
async def test_injected_canonical_arguments_require_an_object_and_remain_unresolved(arguments):
    injected = FakeModel(
        {
            "tool_calls": [
                {"name": "safe", "arguments": {}},
                {"name": "safe", "arguments": arguments},
            ]
        }
    )
    guarded = BoundedModel(injected, ProviderBoundsConfig())
    with pytest.raises(_ModelOutcomeUncertain) as caught:
        await guarded.complete("original")
    assert caught.value.details["reason"] == "invalid_response" and len(injected.calls) == 1


@pytest.mark.asyncio
async def test_trusted_model_raised_common_error_remains_known_and_sanitized(provider):
    calls, replies = provider
    replies.append(ModelError("known provider rejection", details={"reason": "controlled"}))
    with pytest.raises(ModelError) as caught:
        await LiteLLMModel("provider/unit").complete("original")
    assert not isinstance(caught.value, _ModelOutcomeUncertain)
    assert len(calls) == 1 and caught.value.details["reason"] == "controlled"


@pytest.mark.asyncio
async def test_explicit_common_model_timeout_preserves_unknown_after_one_attempt_and_cleanup():
    stopped = asyncio.Event()

    async def wait(_prompt):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    injected = FakeModel(wait)
    guarded = BoundedModel(injected, ProviderBoundsConfig(timeout_seconds=0.04))
    with pytest.raises(_ModelOutcomeUncertain):
        await guarded.complete("original")
    assert len(injected.calls) == 1 and stopped.is_set()
