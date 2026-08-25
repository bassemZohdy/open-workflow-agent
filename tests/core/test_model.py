from __future__ import annotations

import importlib.util

import pytest
from open_workflow_agent.catalog import CatalogContext, FakeModel, LiteLLMModel
from open_workflow_agent.errors import ModelError


@pytest.mark.asyncio
async def test_fake_model_is_deterministic():
    model = FakeModel({"answer": 42})
    assert await model.complete("ignored") == {"answer": 42}
    assert model.calls == ["ignored"]


@pytest.mark.asyncio
async def test_litellm_import_is_deferred_and_clear():
    if importlib.util.find_spec("litellm") is not None:
        pytest.skip("LiteLLM is installed; provider invocation is not a deterministic test")
    model = LiteLLMModel("provider/model")
    with pytest.raises(ModelError) as error:
        await model.complete("hello")
    assert "LiteLLM" in str(error.value)


@pytest.mark.asyncio
async def test_fake_model_supports_deterministic_tool_rounds(services):
    services.model = FakeModel(
        [
            {"tool_call": {"name": "search_knowledge", "arguments": {"query": "policy"}}},
            {"response": "tool result incorporated"},
        ]
    )
    services.catalog = services.catalog.default(services.model, services=services)
    result = await services.catalog.call(
        "agent:1.0.0@default",
        {"input": "find the policy"},
        CatalogContext(model=services.model, services=services),
    )
    assert result == {"response": "tool result incorporated"}
    assert len(services.model.calls) == 2
