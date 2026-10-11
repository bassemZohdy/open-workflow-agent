from openworkflow_adk import load
from openworkflow_adk.internal import build_workflow


def test_competing_fork_is_single_coordinator_node() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "race", "version": "1.0.0"},
            "do": [
                {
                    "race": {
                        "fork": {
                            "compete": True,
                            "branches": [{"fast": {"wait": {"seconds": 0}}}],
                        }
                    }
                }
            ],
        }
    )

    workflow = build_workflow(document)
    assert {node.name for node in workflow.graph.nodes} == {"__START__", "race"}


def test_nested_do_is_wrapped_as_executable_node() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "nested", "version": "1.0.0"},
            "do": [{"group": {"do": [{"inside": {"wait": {"seconds": 0}}}]}}],
        }
    )

    workflow = build_workflow(document)
    assert {node.name for node in workflow.graph.nodes} == {"__START__", "group"}
