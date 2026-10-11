from openworkflow_adk import load
from openworkflow_adk.internal import build_workflow


def test_non_competing_fork_fans_out_and_joins() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "fork", "version": "1.0.0"},
            "do": [
                {
                    "parallel": {
                        "fork": {
                            "branches": [
                                {"left": {"wait": {"seconds": 0}}},
                                {"right": {"wait": {"seconds": 0}}},
                            ]
                        }
                    }
                },
                {"done": {"wait": {"seconds": 0}, "then": "exit"}},
            ],
        }
    )

    workflow = build_workflow(document)
    names = {node.name for node in workflow.graph.nodes}
    assert {"left", "right", "parallel__join", "done"} <= names
