from types import SimpleNamespace

import pytest
from open_workflow_agent.config import AgentConfig, ModelConfig
from open_workflow_agent.engine import InvocationResult
from open_workflow_agent.errors import InvocationStateError
from open_workflow_agent.persistence import ExecutionHandle
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
async def test_native_resume_failure_callsite_logs_only_safe_diagnostic_fields(caplog, monkeypatch):
    caplog.set_level("WARNING", logger="open_workflow_agent_adk")
    handle = ExecutionHandle(
        invocation_id="private-invocation-id-sentinel",
        engine="adk",
        engine_execution_reference="private-native-reference-sentinel",
        user_id="private-user-id-sentinel",
        session_id="private-session-id-sentinel",
        workflow_name="test",
        workflow_version="1.0.0",
        workflow_fingerprint="f" * 64,
    )
    workflow_input = {"private-input-sentinel": "private-input-value"}
    failure_message = (
        "private native state and bearer secret "
        + handle.invocation_id
        + " "
        + handle.engine_execution_reference
        + " "
        + handle.session_id
        + " "
        + handle.user_id
        + " "
        + str(workflow_input)
    )

    class FailingNativeRunner:
        available = True

        async def resume(self, *_args, **_kwargs):
            raise InvocationStateError(
                failure_message,
                details={"native_reference": handle.engine_execution_reference},
            )

    engine = AdkWorkflowEngine()
    engine.native = FailingNativeRunner()
    engine.services = SimpleNamespace(
        execution_ownership=SimpleNamespace(native_fence=lambda _lease: None)
    )
    engine._execution_leases[handle.invocation_id] = object()

    async def prepare_input(_plan, _handle, _resume_input):
        return workflow_input

    monkeypatch.setattr(engine, "_prepare_workflow_input_owned", prepare_input)
    monkeypatch.setattr(engine, "_metadata", lambda *_args: {})
    monkeypatch.setattr(engine, "_session_database_path", lambda: "private-db-path")
    monkeypatch.setattr(engine, "_native_user", lambda _handle: "private-native-user")
    monkeypatch.setattr(engine, "_recovery_context", lambda *_args: {})
    monkeypatch.setattr(
        engine,
        "_record_result",
        lambda current, **values: InvocationResult(
            current.invocation_id,
            current.session_id,
            values["status"],
            error=values.get("error"),
        ),
    )

    result = await engine._resume_owned(handle, {"private-resume-sentinel": True}, None)

    record = next(
        record for record in caplog.records if record.message == "ADK invocation resume failed"
    )
    assert record.extra_fields == {
        "stage": "native_resume",
        "error_type": "InvocationStateError",
        "error_code": "invalid_invocation_state",
    }
    assert result.status == "faulted"
    for sentinel in (
        "private native state",
        "bearer secret",
        "private-invocation-id-sentinel",
        "private-native-reference-sentinel",
        "private-session-id-sentinel",
        "private-user-id-sentinel",
        "private-input-sentinel",
        "private-input-value",
        "private-resume-sentinel",
        "private-db-path",
        "private-native-user",
    ):
        assert sentinel not in caplog.text


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
