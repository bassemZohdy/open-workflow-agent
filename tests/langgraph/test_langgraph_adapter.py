from open_workflow_agent.config import AgentConfig, ModelConfig
from open_workflow_agent.tools import AgentToolBinding
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


def test_channel_extension_advertisement_tracks_native_backend_availability(monkeypatch):
    import open_workflow_agent_langgraph.native as native

    engine = LangGraphWorkflowEngine()
    monkeypatch.setattr(native, "LANGGRAPH_AVAILABLE", True)
    assert engine.capabilities().as_dict()["nativeExtensions"] == ["langgraph.channels@1.0.0"]

    monkeypatch.setattr(native, "LANGGRAPH_AVAILABLE", False)
    assert engine.capabilities().as_dict()["nativeExtensions"] == []


def test_langgraph_factory_binds_real_structured_tools():
    async def invoke(payload):
        return payload

    tools = LangGraphAgentFactory().bind_tools(
        (AgentToolBinding("configured", "configured tool", invoke),)
    )
    assert tools
    assert tools[0].name == "configured"


def test_native_backend_availability_does_not_require_sqlite_for_postgres(monkeypatch):
    import open_workflow_agent_langgraph.native as native

    monkeypatch.setattr(native, "LANGGRAPH_AVAILABLE", True)
    monkeypatch.setattr(native, "AsyncSqliteSaver", None)
    monkeypatch.setattr(native, "AsyncPostgresSaver", object())
    adapter = LangGraphFunctionalAdapter()
    assert adapter.task_nodes_available
    adapter.database_path = "postgresql://example.test/runtime"
    assert adapter.task_nodes_available
    adapter.database_path = "local.sqlite"
    assert not adapter.task_nodes_available
    monkeypatch.setattr(native, "AsyncSqliteSaver", object())
    assert adapter.task_nodes_available
    adapter.database_path = "postgresql://example.test/runtime"
    monkeypatch.setattr(native, "AsyncPostgresSaver", None)
    assert not adapter.task_nodes_available
