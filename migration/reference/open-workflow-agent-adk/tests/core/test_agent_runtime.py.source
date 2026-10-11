from collections.abc import AsyncGenerator

from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from openworkflow_adk import load, run_workflow


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


async def test_agent_task_runs_with_injected_stub_model() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "agent-run",
                "version": "1.0.0",
            },
            "do": [
                {
                    "answer": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {"agent": {"model": "stub-model", "instruction": "Answer."}}
                        },
                    }
                }
            ],
        }
    )

    events = await run_workflow(document, model_factory=lambda _: StubLlm(model="stub-model"))

    assert any(event.content and event.content.parts[0].text == "stub answer" for event in events)


async def test_agent_events_can_be_streamed_to_an_async_sink() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "stream-agent",
                "version": "1.0.0",
            },
            "do": [
                {
                    "answer": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {"agent": {"model": "stub-model", "instruction": "Answer."}}
                        },
                    }
                }
            ],
        }
    )
    received = []

    async def sink(event) -> None:
        received.append(event)

    events = await run_workflow(
        document,
        model_factory=lambda _: StubLlm(model="stub-model"),
        event_sink=sink,
    )

    assert received
    assert received == events
