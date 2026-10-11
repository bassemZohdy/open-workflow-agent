from openworkflow_adk import debate_pattern, load, map_reduce_pattern


def test_map_reduce_fragment_is_composable() -> None:
    fragment = map_reduce_pattern(
        "map_items", ".items", [{"save": {"set": {"value": ".item"}}}], {"set": {"done": "true"}}
    )
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "patterns",
                "version": "1.0.0",
            },
            "do": fragment,
        }
    )
    assert [item.name for item in document.do] == ["map_items", "map_items_reduce"]


def test_debate_fragment_builds_a_coordinator_tree() -> None:
    fragment = debate_pattern("debate", "model", [{"name": "critic", "instruction": "Critique."}])
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "debate",
                "version": "1.0.0",
            },
            "do": [fragment],
        }
    )
    assert document.do[0].task.effective_agent().sub_agents[0].name == "critic"
