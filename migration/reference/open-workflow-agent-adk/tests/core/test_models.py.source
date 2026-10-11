import pytest

from openworkflow_adk import DocumentAdkMetadata, ModelReference, ModelSpec, TaskAdkMetadata, load
from openworkflow_adk.internal import build_workflow, resolve_agent_characteristics


def test_named_model_reference_resolves_and_environment_can_repoint_bundle() -> None:
    spec = ModelSpec(model="gemini-fast", generate_content_config={"temperature": 0.1})
    resolved = resolve_agent_characteristics(
        {"model": {"use": "fast"}, "instruction": "task"},
        models={"fast": spec},
        environ={"WORKFLOW_MODELS__FAST__MODEL": "gemini-deployment"},
    )

    assert resolved.model == "gemini-deployment"
    assert resolved.generate_content_config == {"temperature": 0.1}


def test_model_reference_and_literal_model_are_distinct_shapes() -> None:
    assert ModelReference(use="fast").use == "fast"
    assert resolve_agent_characteristics({"model": "fast"}).model == "fast"


def test_unknown_model_reference_is_a_structured_load_error() -> None:
    with pytest.raises(ValueError, match="unknown model reference"):
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "unknown-model",
                    "version": "1.0.0",
                },
                "do": [
                    {
                        "agent": {
                            "wait": {"seconds": 0},
                            "metadata": {"adk": {"agent": {"model": {"use": "missing"}}}},
                        }
                    }
                ],
            }
        )


def test_workflow_agent_uses_named_model_bundle() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "model-bundle",
                "version": "1.0.0",
                "metadata": {
                    "adk": {
                        "models": {
                            "fast": {
                                "model": "resolved-model",
                                "generate_content_config": {"temperature": 0.2},
                            }
                        }
                    }
                },
            },
            "do": [
                {
                    "answer": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {"agent": {"model": {"use": "fast"}, "instruction": "Answer."}}
                        },
                    }
                }
            ],
        }
    )

    workflow = build_workflow(document, model_factory=lambda model: f"factory:{model}")
    agent = workflow.edges[0][1]
    assert agent.model == "factory:resolved-model"
    assert agent.generate_content_config.temperature == 0.2


def test_task_adk_metadata_rejects_document_only_fields() -> None:
    with pytest.raises(ValueError, match="models"):
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "bad-task-meta",
                    "version": "1.0.0",
                },
                "do": [
                    {
                        "answer": {
                            "wait": {"seconds": 0},
                            "metadata": {
                                "adk": {
                                    "agent": {"instruction": "hi"},
                                    "models": {"fast": {"model": "gemini"}},
                                }
                            },
                        }
                    }
                ],
            }
        )


def test_document_adk_metadata_rejects_task_only_fields() -> None:
    with pytest.raises(ValueError, match="agent"):
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "bad-doc-meta",
                    "version": "1.0.0",
                    "metadata": {
                        "adk": {
                            "agent": {"instruction": "hi"},
                            "models": {"fast": {"model": "gemini"}},
                        }
                    },
                },
                "do": [{"answer": {"wait": {"seconds": 0}}}],
            }
        )


def test_task_adk_metadata_accepts_agent_and_self_heal() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "task-meta",
                "version": "1.0.0",
            },
            "do": [
                {
                    "answer": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {
                                "agent": {"instruction": "hi"},
                                "self_heal": {"max_attempts": 2},
                            }
                        },
                    }
                }
            ],
        }
    )
    task_adk = document.do[0].task.adk_metadata()
    assert isinstance(task_adk, TaskAdkMetadata)
    assert task_adk.agent is not None
    assert task_adk.agent.instruction == "hi"
    assert task_adk.self_heal == {"max_attempts": 2}


def test_document_adk_metadata_accepts_registries() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "doc-meta",
                "version": "1.0.0",
                "metadata": {
                    "adk": {
                        "models": {"fast": {"model": "gemini"}},
                        "providers": {"gemini": {"type": "gemini"}},
                        "memories": {"session": {"type": "in-memory"}},
                    }
                },
            },
            "do": [{"answer": {"wait": {"seconds": 0}}}],
        }
    )
    doc_adk = document.adk_metadata()
    assert isinstance(doc_adk, DocumentAdkMetadata)
    assert doc_adk.models is not None
    assert doc_adk.models["fast"].model == "gemini"
    assert doc_adk.providers is not None
    assert doc_adk.providers["gemini"].type == "gemini"
    assert doc_adk.memories is not None
    assert doc_adk.memories["session"].type == "in-memory"
