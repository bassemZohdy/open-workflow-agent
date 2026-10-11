import pytest

from openworkflow_adk import WorkflowValidationError, load
from openworkflow_adk.internal import resolve_memory_config, resolve_provider_config


def test_provider_and_memory_registries_parse_and_environment_overrides() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "registries",
                "version": "1.0.0",
                "metadata": {
                    "adk": {
                        "providers": {
                            "openai-prod": {"type": "openai", "base_url": "https://one.example"}
                        },
                        "memories": {"local": {"type": "file", "connection": "/tmp/memory"}},
                    }
                },
            },
            "do": [{"save": {"set": {"value": "1"}}}],
        }
    )
    environ = {"WORKFLOW_PROVIDERS__OPENAI-PROD__BASE_URL": "https://two.example"}

    provider = resolve_provider_config("openai-prod", document.effective_providers(), environ)
    memory = resolve_memory_config("local", document.effective_memories())

    assert provider.base_url == "https://two.example"
    assert memory.type == "file"


def test_unknown_provider_type_and_reference_are_structured() -> None:
    with pytest.raises(WorkflowValidationError) as invalid_type:
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "bad",
                    "version": "1.0.0",
                    "metadata": {"adk": {"providers": {"bad": {"type": "unknown"}}}},
                },
                "do": [{"save": {"set": {"value": "1"}}}],
            }
        )
    assert "unknown provider type" in str(invalid_type.value)

    with pytest.raises(WorkflowValidationError, match="unknown memory reference"):
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "bad-ref",
                    "version": "1.0.0",
                },
                "do": [
                    {
                        "agent": {
                            "wait": {"seconds": 0},
                            "metadata": {
                                "adk": {"agent": {"memory": {"use": "missing"}, "instruction": "x"}}
                            },
                        }
                    }
                ],
            }
        )
