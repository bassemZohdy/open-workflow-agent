from openworkflow_adk import load
from openworkflow_adk.internal import NodeBuilderRegistry, build_workflow


def test_registry_has_all_task_kinds() -> None:
    registry = NodeBuilderRegistry()

    assert {
        "call",
        "do",
        "fork",
        "emit",
        "for",
        "listen",
        "raise",
        "run",
        "set",
        "switch",
        "try",
        "wait",
    } <= set(registry.keys())


def test_sequence_builder_honors_named_goto_and_terminal() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "flow", "version": "1.0.0"},
            "do": [
                {"first": {"wait": {"seconds": 0}, "then": "last"}},
                {"middle": {"wait": {"seconds": 0}}},
                {"last": {"wait": {"seconds": 0}, "then": "exit"}},
            ],
        }
    )

    workflow = build_workflow(document)
    assert [node.name for node in workflow.graph.nodes if node.name != "__START__"] == [
        "first",
        "last",
    ]
    assert [(edge.from_node.name, edge.to_node.name) for edge in workflow.graph.edges] == [
        ("__START__", "first"),
        ("first", "last"),
    ]
