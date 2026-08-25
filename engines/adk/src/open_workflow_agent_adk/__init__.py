"""ADK engine adapter."""

from open_workflow_agent.engine import EngineCapabilities, PortableWorkflowEngine


class AdkWorkflowEngine(PortableWorkflowEngine):
    """ADK-facing engine boundary.

    The adapter preserves the ADK boundary and capability contract. When the
    optional ADK package is installed, native compilation can be supplied
    here; the reference implementation uses the common portable executor so
    tests remain deterministic and do not require a provider.
    """

    engine_name = "adk"

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(engine=self.engine_name, resume=True, streaming=False)


__all__ = ["AdkWorkflowEngine"]
