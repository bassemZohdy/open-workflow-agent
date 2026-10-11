from collections.abc import AsyncGenerator

from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from openworkflow_adk import load, run_workflow


class RouteLlm(BaseLlm):
    _calls: int = PrivateAttr(0)

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["route-model"]

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
                                name="route_to", args={"route": "approve"}
                            )
                        )
                    ],
                )
            )
            return
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="routed")]))


async def test_agent_can_select_a_switch_route() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "agent-route",
                "version": "1.0.0",
            },
            "do": [
                {
                    "decide": {
                        "switch": [
                            {"approve": {"then": "approved"}},
                            {"default": {"then": "rejected"}},
                        ],
                        "metadata": {
                            "adk": {
                                "agent": {"model": "route-model", "instruction": "Choose a route."}
                            }
                        },
                    }
                },
                {"approved": {"set": {"result": '"approved"'}}},
                {"rejected": {"set": {"result": '"rejected"'}}},
            ],
        }
    )
    model = RouteLlm(model="route-model")
    events = await run_workflow(document, model_factory=lambda _: model)

    assert any(
        event.actions and event.actions.state_delta.get("result") == "approved" for event in events
    )
