from __future__ import annotations

import importlib.util

import pytest
from open_workflow_agent.catalog import FakeModel, LiteLLMModel
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
