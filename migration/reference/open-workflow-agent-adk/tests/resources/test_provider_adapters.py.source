import boto3
import respx
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from httpx import Response

from openworkflow_adk import AnthropicLlm, BedrockLlm, ProviderConfig
from openworkflow_adk.internal import create_llm


def test_anthropic_and_bedrock_adapters_construct_from_provider_config(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_SECRET__anthropic-key", "secret")
    anthropic = create_llm(
        "claude-test",
        ProviderConfig(type="anthropic", credential="anthropic-key"),
    )
    bedrock = create_llm(
        "amazon.test", ProviderConfig(type="bedrock", extra={"region": "us-east-1"})
    )

    assert isinstance(anthropic, AnthropicLlm)
    assert isinstance(bedrock, BedrockLlm)


async def test_anthropic_adapter_calls_messages_api(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_SECRET__anthropic-key", "secret")
    with respx.mock(assert_all_called=True) as router:
        router.post("https://llm.example/v1/messages").mock(
            return_value=Response(200, json={"content": [{"text": "hello"}]})
        )
        model = create_llm(
            "claude-test",
            ProviderConfig(
                type="anthropic", base_url="https://llm.example", credential="anthropic-key"
            ),
        )
        response = [
            item
            async for item in model.generate_content_async(
                LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="hi")])])
            )
        ]
    assert response[0].content.parts[0].text == "hello"


async def test_bedrock_adapter_uses_converse(monkeypatch) -> None:
    class FakeClient:
        def converse(self, **kwargs):
            assert kwargs["modelId"] == "amazon.test"
            return {"output": {"message": {"content": [{"text": "hello"}]}}}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeClient())
    model = create_llm("amazon.test", ProviderConfig(type="bedrock"))
    response = [
        item
        async for item in model.generate_content_async(
            LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="hi")])])
        )
    ]
    assert response[0].content.parts[0].text == "hello"
