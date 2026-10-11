"""Deterministic agent used by the ADK evaluation gate."""

from collections.abc import AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


class EvaluationLlm(BaseLlm):
    @classmethod
    def supported_models(cls) -> list[str]:
        return ["evaluation-stub"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="evaluation answer")])
        )


root_agent = LlmAgent(
    name="evaluation_agent",
    model=EvaluationLlm(model="evaluation-stub"),
    instruction="Answer deterministically.",
)
agent = root_agent
