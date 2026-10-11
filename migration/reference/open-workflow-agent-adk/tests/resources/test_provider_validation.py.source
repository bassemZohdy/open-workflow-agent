import pytest

from openworkflow_adk import WorkflowValidationError, load


def test_unknown_model_provider_reference_is_structured() -> None:
    with pytest.raises(WorkflowValidationError, match="unknown provider reference"):
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "bad-provider",
                    "version": "1.0.0",
                    "metadata": {
                        "adk": {
                            "models": {"model": {"model": "gpt", "provider": {"use": "missing"}}}
                        }
                    },
                },
                "do": [
                    {
                        "answer": {
                            "wait": {"seconds": 0},
                            "metadata": {
                                "adk": {"agent": {"model": {"use": "model"}, "instruction": "x"}}
                            },
                        }
                    }
                ],
            }
        )
