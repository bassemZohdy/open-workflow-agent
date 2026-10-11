from openworkflow_adk import load, simplify_workflow


def test_simplifier_removes_provably_unreachable_and_noop_tasks() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "simplify",
                "version": "1.0.0",
            },
            "do": [
                {"stop": {"then": "end", "set": {"done": '"yes"'}}},
                {"dead": {"set": {"never": '"run"'}}},
                {"pause": {"wait": {"seconds": 0}}},
            ],
        }
    )

    result = simplify_workflow(document)

    assert [item.name for item in result.document.do] == ["stop"]
    assert len(result.changes) == 2
