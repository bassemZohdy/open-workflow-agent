from google.adk.workflow._graph import DEFAULT_ROUTE

from openworkflow_adk import load
from openworkflow_adk.internal import build_workflow


def test_switch_builds_routed_edges() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "switch", "version": "1.0.0"},
            "do": [
                {
                    "choose": {
                        "switch": [
                            {"electronic": {"when": '.kind == "electronic"', "then": "electronic"}},
                            {"default": {"then": "fallback"}},
                        ]
                    }
                },
                {"electronic": {"wait": {"seconds": 0}, "then": "exit"}},
                {"fallback": {"wait": {"seconds": 0}, "then": "exit"}},
            ],
        }
    )

    workflow = build_workflow(document)
    routed = [edge for edge in workflow.graph.edges if edge.from_node.name == "choose"]
    assert {edge.route for edge in routed} == {"electronic", DEFAULT_ROUTE}
