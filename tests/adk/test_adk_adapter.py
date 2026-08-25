from open_workflow_agent.config import AgentConfig, ModelConfig
from open_workflow_agent.tools import AgentToolBinding
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_adk.agent import AdkAgentFactory
from open_workflow_agent_adk.model import AdkModelAdapter
from open_workflow_agent_adk.native import ADK_AVAILABLE, NativeAdkRunner


def test_adk_factory_and_adapter_preserve_common_contract():
    agent = AdkAgentFactory().create(AgentConfig(name="support"), ModelConfig(name="fake/test"))
    assert agent.name == "support"
    assert AdkWorkflowEngine().capabilities().engine == "adk"
    assert AdkModelAdapter(ModelConfig()).config.name == "fake/default"
    assert NativeAdkRunner().available is ADK_AVAILABLE


def test_adk_factory_binds_real_function_tools():
    async def invoke(payload):
        return payload

    tools = AdkAgentFactory().bind_tools(
        (AgentToolBinding("configured", "configured tool", invoke),)
    )
    assert tools
    assert getattr(tools[0], "name", "configured") == "configured"
