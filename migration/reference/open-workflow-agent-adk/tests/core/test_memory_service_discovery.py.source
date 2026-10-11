from openworkflow_adk import load
from openworkflow_adk.internal import memory_service_for_document
from openworkflow_adk.resources.memory import FileMemoryService, InMemoryMemoryService


def _document_with_memory() -> dict:
    return {
        "document": {
            "dsl": "1.0.3",
            "namespace": "demo",
            "name": "nested-memory",
            "version": "1.0.0",
            "metadata": {
                "adk": {
                    "memories": {"short": {"type": "in-memory"}},
                }
            },
        },
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
                                            "memory": {"use": "short"},
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


def test_memory_service_discovered_from_nested_agent() -> None:
    document = load(_document_with_memory())

    service = memory_service_for_document(document)

    assert isinstance(service, InMemoryMemoryService)


def test_memory_service_applies_environment_overrides() -> None:
    document = load(_document_with_memory())

    service = memory_service_for_document(
        document,
        environ={
            "WORKFLOW_MEMORIES__SHORT__TYPE": "file",
            "WORKFLOW_MEMORIES__SHORT__CONNECTION": "/tmp/owf-mem.json",
        },
    )

    assert isinstance(service, FileMemoryService)
