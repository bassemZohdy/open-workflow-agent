from google.adk.workflow._function_node import FunctionNode

from openworkflow_adk import load
from openworkflow_adk.internal import NodeBuilderRegistry, build_workflow


def test_plugin_call_builder_can_add_extension_scheme() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "plugin",
                "version": "1.0.0",
            },
            "do": [{"query": {"call": "graphql", "with": {"query": "query"}}}],
        }
    )
    registry = NodeBuilderRegistry()
    registry.register_call(
        "graphql", lambda name, task: FunctionNode(func=lambda ctx: None, name=name)
    )

    workflow = build_workflow(document, registry=registry)

    assert workflow is not None
