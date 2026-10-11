from openworkflow_adk import load
from openworkflow_adk.internal import NodeBuilderRegistry


def test_agent_tool_name_resolves_from_function_registry() -> None:
    def lookup(value: str) -> str:
        return value

    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "tools",
                "version": "1.0.0",
            },
            "do": [
                {
                    "answer": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {
                                "agent": {
                                    "model": "stub",
                                    "instruction": "Answer.",
                                    "tools": ["lookup"],
                                }
                            }
                        },
                    }
                }
            ],
        }
    )

    node = NodeBuilderRegistry(function_registry={"lookup": lookup}).build(
        "answer", document.do[0].task
    )

    assert node.tools == [lookup]
