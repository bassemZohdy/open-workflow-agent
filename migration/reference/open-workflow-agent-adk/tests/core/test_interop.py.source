"""Tests for OpenWorkflow-compatible ADK-extension encoding (C18)."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from openworkflow_adk import load, run_workflow
from openworkflow_adk.schema import load_schema


class StubLlm(BaseLlm):
    @classmethod
    def supported_models(cls) -> list[str]:
        return ["stub-model"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="stub answer")])
        )


async def test_metadata_adk_encoding_runs_end_to_end() -> None:
    """metadata.adk registries + task metadata.adk.agent translate and run."""
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "interop-run",
                "version": "1.0.0",
                "metadata": {
                    "adk": {
                        "models": {"stub": {"model": "stub-model"}},
                        "providers": {
                            "stub-provider": {"type": "openai", "base_url": "http://example.com"}
                        },
                    }
                },
            },
            "do": [
                {
                    "answer": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {
                                "agent": {
                                    "model": {"use": "stub"},
                                    "instruction": "Answer.",
                                }
                            }
                        },
                    }
                }
            ],
        }
    )

    assert document.effective_models()["stub"].model == "stub-model"
    assert document.effective_providers()["stub-provider"].type == "openai"
    agent_config = document.do[0].task.effective_agent()
    assert agent_config is not None
    assert agent_config.model.use == "stub"

    events = await run_workflow(document, model_factory=lambda _: StubLlm(model="stub-model"))

    assert any(
        event.content and event.content.parts and event.content.parts[0].text == "stub answer"
        for event in events
    )


def test_pure_openworkflow_loads_without_adk_metadata() -> None:
    """A document without any ADK extension loads as pure OpenWorkflow."""
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "pure",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"value": 1}}}],
        }
    )

    assert document.do[0].name == "finish"
    assert document.do[0].task.set == {"value": 1}
    assert document.adk_metadata() is None


def test_adk_metadata_passes_openworkflow_schema() -> None:
    """A document with metadata.adk validates against the vendored upstream schema."""
    raw = {
        "document": {
            "dsl": "1.0.3",
            "namespace": "demo",
            "name": "schema-valid",
            "version": "1.0.0",
            "metadata": {
                "adk": {
                    "models": {"stub": {"model": "stub-model"}},
                    "providers": {"stub": {"type": "openai"}},
                }
            },
        },
        "do": [
            {
                "answer": {
                    "wait": {"seconds": 0},
                    "metadata": {"adk": {"agent": {"model": "stub-model", "instruction": "Hi."}}},
                }
            }
        ],
    }

    import jsonschema

    schema = load_schema()
    jsonschema.Draft202012Validator(schema).validate(raw)
