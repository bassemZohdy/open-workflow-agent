from collections.abc import AsyncGenerator

from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from openworkflow_adk import InMemoryRunHistory, load, run_workflow


class ApprovalLlm(BaseLlm):
    _calls: int = PrivateAttr(0)

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["approval-model"]

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
                                name="request_input", args={"_question": "Approve?"}
                            )
                        )
                    ],
                )
            )
            return
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="approved")]))


async def test_agent_can_suspend_for_external_input_and_resume() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "approval",
                "version": "1.0.0",
            },
            "do": [
                {
                    "approve": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {
                                "agent": {
                                    "model": "approval-model",
                                    "instruction": "Ask for approval.",
                                    "request_input": {"question": "Approve?"},
                                }
                            }
                        },
                    }
                }
            ],
        }
    )
    history = InMemoryRunHistory()
    model = ApprovalLlm(model="approval-model")

    await run_workflow(
        document, history=history, session_id="approval", model_factory=lambda _: model
    )

    suspended = history.get("approval")
    assert suspended.status == "suspended"
    assert suspended.suspension_reason == "human_input"

    events = await run_workflow(
        document,
        history=history,
        session_id="approval",
        model_factory=lambda _: model,
        resume=True,
        resume_input={"approved": True},
    )

    assert any(event.content and event.content.parts[0].text == "approved" for event in events)
    assert history.get("approval").status == "completed"
