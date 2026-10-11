from google.adk.agents import LlmAgent

from openworkflow_adk import load
from openworkflow_adk.internal import NodeBuilderRegistry, build_workflow


def test_agent_task_builds_single_turn_llm_agent() -> None:
    document = load(
        {
            "document": {"dsl": "1.0.3", "namespace": "demo", "name": "agent", "version": "1.0.0"},
            "do": [
                {
                    "summarize": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {"agent": {"model": "stub-model", "instruction": "Summarize."}}
                        },
                    }
                }
            ],
        }
    )

    node = NodeBuilderRegistry().build("summarize", document.do[0].task)
    assert isinstance(node, LlmAgent)
    assert node.mode == "single_turn"
    assert node.output_key == "summarize"


def test_document_agent_defaults_supply_missing_fields() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "agent-defaults",
                "version": "1.0.0",
                "metadata": {
                    "adk": {
                        "agent_defaults": {
                            "model": "default-model",
                            "instruction": "Default instruction.",
                        }
                    }
                },
            },
            "do": [
                {
                    "summarize": {
                        "wait": {"seconds": 0},
                        "metadata": {"adk": {"agent": {"description": "task desc"}}},
                    }
                }
            ],
        }
    )

    workflow = build_workflow(document)
    node = next(node for node in workflow.graph.nodes if node.name == "summarize")
    assert isinstance(node, LlmAgent)
    assert node.model == "default-model"
    assert node.instruction == "Default instruction."


def test_task_agent_overrides_document_defaults() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "agent-defaults",
                "version": "1.0.0",
                "metadata": {
                    "adk": {
                        "agent_defaults": {
                            "model": "default-model",
                            "instruction": "Default instruction.",
                        }
                    }
                },
            },
            "do": [
                {
                    "summarize": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {
                                "agent": {
                                    "model": "task-model",
                                    "instruction": "Task instruction.",
                                }
                            }
                        },
                    }
                }
            ],
        }
    )

    workflow = build_workflow(document)
    node = next(node for node in workflow.graph.nodes if node.name == "summarize")
    assert isinstance(node, LlmAgent)
    assert node.model == "task-model"
    assert node.instruction == "Task instruction."
