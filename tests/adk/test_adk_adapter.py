from open_workflow_agent.config import AgentConfig, ModelConfig
from open_workflow_agent_adk import AdkWorkflowEngine
from open_workflow_agent_adk.agent import AdkAgentFactory
from open_workflow_agent_adk.model import AdkModelAdapter


def test_adk_factory_and_adapter_preserve_common_contract():
    agent = AdkAgentFactory().create(AgentConfig(name="support"), ModelConfig(name="fake/test"))
    assert agent.name == "support"
    assert AdkWorkflowEngine().capabilities().engine == "adk"
    assert AdkModelAdapter(ModelConfig()).config.name == "fake/default"
