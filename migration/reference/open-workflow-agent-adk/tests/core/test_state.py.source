from openworkflow_adk import derive_state_schema, load


def test_state_schema_contains_input_and_set_keys() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "state", "version": "1.0.0"},
            "input": {"from": ".payload"},
            "do": [{"prepare": {"set": {"status": '"ready"'}}}],
        }
    )

    schema = derive_state_schema(document)
    assert {"payload", "status"} <= set(schema.model_fields)


def test_state_schema_includes_nested_agent_output_key() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "nested", "version": "1.0.0"},
            "do": [
                {
                    "outer": {
                        "do": [
                            {
                                "inner": {
                                    "wait": {"seconds": 0},
                                    "metadata": {
                                        "adk": {
                                            "agent": {
                                                "model": "gemini-2.5-flash",
                                                "instruction": "hi",
                                                "output_key": "inner_result",
                                            }
                                        }
                                    },
                                }
                            }
                        ]
                    }
                }
            ],
        }
    )

    schema = derive_state_schema(document)
    assert "inner_result" in schema.model_fields


def test_state_schema_includes_switch_when_keys() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "switch", "version": "1.0.0"},
            "do": [
                {
                    "choose": {
                        "switch": [
                            {
                                "approved": {
                                    "when": "${ .approved = true }",
                                    "then": "accepted",
                                }
                            }
                        ]
                    }
                }
            ],
        }
    )

    schema = derive_state_schema(document)
    assert "approved" in schema.model_fields
