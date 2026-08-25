from open_workflow_agent.config import AgentConfig, ModelConfig
from open_workflow_agent_langgraph import LangGraphWorkflowEngine
from open_workflow_agent_langgraph.agent import LangGraphAgentFactory
from open_workflow_agent_langgraph.model import LangGraphModelAdapter
from open_workflow_agent_langgraph.native import LangGraphFunctionalAdapter


def test_langgraph_factory_and_native_bridge_are_optional():
    agent = LangGraphAgentFactory().create(
        AgentConfig(name="support"), ModelConfig(name="fake/test")
    )
    assert agent.name == "support"
    assert LangGraphWorkflowEngine().capabilities().engine == "langgraph"
    assert LangGraphModelAdapter(ModelConfig()).config.name == "fake/default"
    assert LangGraphFunctionalAdapter().compile(lambda value: value) is not None
