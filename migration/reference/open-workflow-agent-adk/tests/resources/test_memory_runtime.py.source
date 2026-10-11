from openworkflow_adk import load
from openworkflow_adk.internal import memory_service_for_document


def test_agent_memory_reference_selects_memory_service() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "memory-agent",
                "version": "1.0.0",
                "metadata": {"adk": {"memories": {"local": {"type": "in-memory"}}}},
            },
            "do": [
                {
                    "agent-task": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {
                                "agent": {
                                    "model": "stub",
                                    "instruction": "remember",
                                    "memory": {"use": "local"},
                                }
                            }
                        },
                    }
                }
            ],
        }
    )

    assert memory_service_for_document(document).__class__.__name__ == "InMemoryMemoryService"
