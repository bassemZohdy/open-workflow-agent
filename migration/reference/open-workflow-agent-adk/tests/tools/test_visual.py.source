from openworkflow_adk import graph_to_yaml, load


def test_visual_graph_exports_valid_workflow_yaml() -> None:
    text = graph_to_yaml(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "visual",
                "version": "1.0.0",
            },
            "nodes": [
                {"id": "start", "task": {"begin": {"set": {"ok": '"yes"'}}}},
                {"id": "finish", "task": {"done": {"set": {"done": '"yes"'}}}},
            ],
            "edges": [{"from": "start", "to": "finish"}],
            "start": "start",
        }
    )

    assert load(text).do[0].name == "begin"
