from collections.abc import AsyncGenerator

from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from openworkflow_adk import load, run_workflow


class MultimodalProbe(BaseLlm):
    _saw_image: bool = PrivateAttr(False)

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["multimodal-model"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._saw_image = any(
            part.inline_data is not None
            for content in llm_request.contents
            for part in content.parts or []
        )
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="seen")]))


async def test_agent_receives_native_multimodal_content() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "vision",
                "version": "1.0.0",
            },
            "do": [
                {
                    "inspect": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {
                                "agent": {"model": "multimodal-model", "instruction": "Inspect."}
                            }
                        },
                    }
                }
            ],
        }
    )
    model = MultimodalProbe(model="multimodal-model")
    content = types.Content(
        role="user",
        parts=[types.Part(inline_data=types.Blob(mime_type="image/png", data=b"png"))],
    )

    events = await run_workflow(document, model_factory=lambda _: model, message=content)

    assert model._saw_image
    assert any(event.content and event.content.parts[0].text == "seen" for event in events)
