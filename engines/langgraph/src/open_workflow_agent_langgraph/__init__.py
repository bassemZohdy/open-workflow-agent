"""LangGraph engine adapter."""

from open_workflow_agent.engine import EngineCapabilities, PortableWorkflowEngine

from .native import LangGraphFunctionalAdapter


class LangGraphWorkflowEngine(PortableWorkflowEngine):
    """LangGraph-facing engine boundary.

    The common executor supplies the portable semantics. A native LangGraph
    Functional API compiler can be selected by deployments that need native
    graph persistence; deterministic contract tests remain framework-neutral.
    """

    engine_name = "langgraph"

    def __init__(self) -> None:
        self.native = LangGraphFunctionalAdapter()

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(engine=self.engine_name, resume=True, streaming=False)


__all__ = ["LangGraphWorkflowEngine"]
