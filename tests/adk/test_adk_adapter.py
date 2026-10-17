from types import SimpleNamespace

import pytest
from open_workflow_agent.config import AgentConfig, ModelConfig
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.tools import AgentToolBinding
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_adk.agent import AdkAgentFactory
from open_workflow_agent_adk.model import AdkModelAdapter
from open_workflow_agent_adk.native import (
    ADK_AVAILABLE,
    ADK_RESUME_STRATEGY,
    NativeAdkRunner,
)


def test_adk_factory_and_adapter_preserve_common_contract():
    agent = AdkAgentFactory().create(AgentConfig(name="support"), ModelConfig(name="fake/test"))
    assert agent.name == "support"
    assert AdkWorkflowEngine().capabilities().engine == "adk"
    assert AdkModelAdapter(ModelConfig()).config.name == "fake/default"
    assert NativeAdkRunner().available is ADK_AVAILABLE
    assert NativeAdkRunner().resume_strategy == ADK_RESUME_STRATEGY
    assert NativeAdkRunner().rerun_on_resume is True


def test_adk_factory_binds_real_function_tools():
    async def invoke(payload):
        return payload

    tools = AdkAgentFactory().bind_tools(
        (AgentToolBinding("configured", "configured tool", invoke),)
    )
    assert tools
    assert getattr(tools[0], "name", "configured") == "configured"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_boundary", ["open", "read", "close"])
async def test_native_admission_storage_failures_use_common_sanitized_errors(
    monkeypatch, failure_boundary
):
    closed = []

    class Sessions:
        async def get_session(self, **kwargs):
            if failure_boundary == "read":
                raise RuntimeError("private-storage-error-must-not-leak")
            return SimpleNamespace(
                events=[
                    SimpleNamespace(
                        invocation_id="private-native-reference",
                        author="user",
                        content=SimpleNamespace(parts=[SimpleNamespace(text='{"original":true}')]),
                    )
                ]
            )

        async def close(self):
            closed.append(True)
            if failure_boundary == "close":
                raise RuntimeError("private-storage-error-must-not-leak")

    def service(_path):
        if failure_boundary == "open":
            raise RuntimeError("private-storage-error-must-not-leak")
        return Sessions()

    native = NativeAdkRunner()
    monkeypatch.setattr(native, "_session_service", service)
    with pytest.raises(InvocationStateError) as failure:
        await native.admit_resume(
            {"original": True},
            session_id="session",
            user_id=None,
            invocation_id="private-native-reference",
        )
    assert failure.value.code == "invalid_invocation_state"
    assert failure.value.details == {}
    assert "private-" not in failure.value.message
    assert closed == ([] if failure_boundary == "open" else [True])
