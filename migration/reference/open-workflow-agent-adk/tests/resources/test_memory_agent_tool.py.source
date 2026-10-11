from google.adk.tools import load_memory

from openworkflow_adk import load
from openworkflow_adk.internal import NodeBuilderRegistry


def test_memory_reference_adds_adk_load_memory_tool() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "memory-tool",
                "version": "1.0.0",
                "metadata": {"adk": {"memories": {"local": {"type": "in-memory"}}}},
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
                                    "memory": {"use": "local"},
                                }
                            }
                        },
                    }
                }
            ],
        }
    )

    node = NodeBuilderRegistry().build("answer", document.do[0].task)

    assert load_memory in node.tools
