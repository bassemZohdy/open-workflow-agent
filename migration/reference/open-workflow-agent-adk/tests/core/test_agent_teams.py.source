from collections.abc import AsyncGenerator

from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from openworkflow_adk import load, run_workflow


class HandoffLlm(BaseLlm):
    """First response delegates; the next response is produced by the member."""

    _calls: int = PrivateAttr(0)

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["team-model"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._calls += 1
        if self._calls == 1:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name="transfer_to_agent",
                                args={"agent_name": "researcher"},
                            )
                        )
                    ],
                )
            )
            return
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="research complete")])
        )


async def test_team_agent_transfers_to_sub_agent() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "team",
                "version": "1.0.0",
            },
            "do": [
                {
                    "coordinate": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {
                                "agent": {
                                    "model": "team-model",
                                    "instruction": "Delegate.",
                                    "sub_agents": [
                                        {
                                            "name": "researcher",
                                            "model": "team-model",
                                            "instruction": "Research.",
                                        }
                                    ],
                                }
                            }
                        },
                    }
                }
            ],
        }
    )
    model = HandoffLlm(model="team-model")

    events = await run_workflow(document, model_factory=lambda _: model)

    assert any(
        event.author == "researcher"
        and event.content
        and event.content.parts
        and event.content.parts[0].text == "research complete"
        for event in events
    )
