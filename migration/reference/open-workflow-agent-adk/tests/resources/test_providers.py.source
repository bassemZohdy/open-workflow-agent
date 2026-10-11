import httpx
import pytest
import respx
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from httpx import Response

from openworkflow_adk import OpenAICompatibleLlm, ProviderConfig, load, run_workflow
from openworkflow_adk.internal import build_workflow, create_llm


async def test_openai_compatible_adapter_calls_chat_completions(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_SECRET__openai-key", "secret")
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://llm.example/v1/chat/completions").mock(
            return_value=Response(200, json={"choices": [{"message": {"content": "hello"}}]})
        )
        model = create_llm(
            "gpt-test",
            ProviderConfig(
                type="openai", base_url="https://llm.example/v1", credential="openai-key"
            ),
        )
        assert isinstance(model, OpenAICompatibleLlm)
        response = [
            item
            async for item in model.generate_content_async(
                LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="hi")])])
            )
        ]

    assert route.called
    assert response[0].content.parts[0].text == "hello"


@pytest.mark.parametrize("provider_type", ["openai", "azure", "ollama", "vllm"])
async def test_openai_compatible_provider_family_smoke(monkeypatch, provider_type) -> None:
    monkeypatch.setenv("WORKFLOW_SECRET__key", "secret")
    with respx.mock(assert_all_called=True) as router:
        router.post("https://llm.example/v1/chat/completions").mock(
            return_value=Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        )
        model = create_llm(
            "test-model",
            ProviderConfig(
                type=provider_type,
                base_url="https://llm.example/v1",
                credential="key",
            ),
        )
        result = [
            item
            async for item in model.generate_content_async(
                LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="x")])])
            )
        ]
    assert result[0].content.parts[0].text == "ok"


async def test_openai_provider_runs_through_adk_runner(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_SECRET__openai-key", "secret")
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://llm.example/v1/chat/completions").mock(
            return_value=Response(
                200,
                json={"choices": [{"message": {"content": "runner answer"}}]},
            )
        )
        document = load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "provider-runner",
                    "version": "1.0.0",
                    "metadata": {
                        "adk": {
                            "providers": {
                                "openai-prod": {
                                    "type": "openai",
                                    "base_url": "https://llm.example/v1",
                                    "credential": "openai-key",
                                }
                            },
                            "models": {
                                "gpt4o": {
                                    "model": "gpt-test",
                                    "provider": {"use": "openai-prod"},
                                }
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
                                        "model": {"use": "gpt4o"},
                                        "instruction": "Answer.",
                                    }
                                }
                            },
                        }
                    }
                ],
            }
        )

        events = await run_workflow(document)

    assert route.called
    assert any(
        event.content and event.content.parts and event.content.parts[0].text == "runner answer"
        for event in events
    )


async def test_provider_unreachable_error_is_not_silently_swallowed(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_SECRET__key", "secret")
    with respx.mock() as router:
        router.post("https://llm.example/v1/chat/completions").mock(
            side_effect=httpx.ConnectError("offline")
        )
        model = create_llm(
            "test-model",
            ProviderConfig(type="openai", base_url="https://llm.example/v1", credential="key"),
        )
        with pytest.raises(httpx.ConnectError):
            [
                item
                async for item in model.generate_content_async(
                    LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="x")])])
                )
            ]


def test_model_reference_resolves_provider_to_adk_model(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_SECRET__openai-key", "secret")
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "provider-agent",
                "version": "1.0.0",
                "metadata": {
                    "adk": {
                        "providers": {
                            "openai-prod": {
                                "type": "openai",
                                "base_url": "https://llm.example/v1",
                                "credential": "openai-key",
                            }
                        },
                        "models": {
                            "gpt4o": {"model": "gpt-test", "provider": {"use": "openai-prod"}}
                        },
                    }
                },
            },
            "do": [
                {
                    "answer": {
                        "wait": {"seconds": 0},
                        "metadata": {
                            "adk": {"agent": {"model": {"use": "gpt4o"}, "instruction": "Answer."}}
                        },
                    }
                }
            ],
        }
    )

    workflow = build_workflow(document)

    assert isinstance(workflow.edges[0][1].model, OpenAICompatibleLlm)
