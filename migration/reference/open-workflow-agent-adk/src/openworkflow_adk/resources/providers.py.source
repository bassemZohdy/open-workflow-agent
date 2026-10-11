"""Provider-backed ADK ``BaseLlm`` adapters."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from openworkflow_adk.models import ProviderConfig
from openworkflow_adk.security.security import resolve_secret


class OpenAICompatibleLlm(BaseLlm):
    """Minimal chat-completions adapter shared by OpenAI-compatible APIs."""

    base_url: str = "https://api.openai.com/v1"
    api_key: str
    timeout: float = 60.0

    @classmethod
    def supported_models(cls) -> list[str]:
        return [
            r"openai:[\w.-]+",
            r"azure:[\w.-]+",
            r"ollama:[\w./:-]+",
            r"vllm:[\w./:-]+",
        ]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        messages = [
            {
                "role": content.role or "user",
                "content": "".join(part.text or "" for part in content.parts or []),
            }
            for content in llm_request.contents
        ]
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        config = llm_request.config
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        if config.max_output_tokens is not None:
            payload["max_tokens"] = config.max_output_tokens
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
        text = response.json()["choices"][0]["message"].get("content", "")
        yield LlmResponse(
            modelVersion=self.model,
            content=types.Content(role="model", parts=[types.Part(text=text)]),
            partial=False,
            turnComplete=True,
        )


class AnthropicLlm(BaseLlm):
    """Adapter for the Anthropic Messages API."""

    api_key: str
    base_url: str = "https://api.anthropic.com"
    timeout: float = 60.0

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"anthropic:[\w.-]+", r"claude-[\w.-]+"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        messages = [
            {
                "role": content.role or "user",
                "content": "".join(part.text or "" for part in content.parts or []),
            }
            for content in llm_request.contents
        ]
        config = llm_request.config
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": config.max_output_tokens or 1024,
            "messages": messages,
        }
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=payload,
            )
            response.raise_for_status()
        text = response.json()["content"][0].get("text", "")
        yield LlmResponse(
            modelVersion=self.model,
            content=types.Content(role="model", parts=[types.Part(text=text)]),
            partial=False,
            turnComplete=True,
        )


class BedrockLlm(BaseLlm):
    """AWS Bedrock Converse adapter using boto3's default credential chain."""

    region: str | None = None

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"bedrock:[\w./:-]+"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        import boto3

        messages = [
            {
                "role": content.role or "user",
                "content": [{"text": "".join(part.text or "" for part in content.parts or [])}],
            }
            for content in llm_request.contents
        ]
        kwargs: dict[str, Any] = {"modelId": self.model, "messages": messages}
        config = llm_request.config
        if config.temperature is not None or config.max_output_tokens is not None:
            kwargs["inferenceConfig"] = {
                key: value
                for key, value in {
                    "temperature": config.temperature,
                    "maxTokens": config.max_output_tokens,
                }.items()
                if value is not None
            }
        client = boto3.client("bedrock-runtime", region_name=self.region)
        response = await asyncio.to_thread(client.converse, **kwargs)
        text = response["output"]["message"]["content"][0].get("text", "")
        yield LlmResponse(
            modelVersion=self.model,
            content=types.Content(role="model", parts=[types.Part(text=text)]),
            partial=False,
            turnComplete=True,
        )


def create_llm(model: str, config: ProviderConfig) -> BaseLlm | str:
    """Create an ADK model for a provider configuration."""
    if config.type == "gemini":
        return model
    if config.type == "bedrock":
        return BedrockLlm(model=model, region=config.extra.get("region"))
    if config.type == "anthropic":
        credential = resolve_secret(config.credential) if config.credential else None
        if not credential:
            raise ValueError("provider 'anthropic' requires a resolvable credential reference")
        return AnthropicLlm(
            model=model,
            api_key=credential,
            base_url=config.base_url or config.endpoint or "https://api.anthropic.com",
        )
    if config.type not in {"openai", "azure", "ollama", "vllm"}:
        raise NotImplementedError(f"provider adapter {config.type!r} is not available")
    credential = resolve_secret(config.credential) if config.credential else None
    if not credential:
        raise ValueError(f"provider {config.type!r} requires a resolvable credential reference")
    defaults = {
        "openai": "https://api.openai.com/v1",
        "azure": "https://api.openai.com/v1",
        "ollama": "http://127.0.0.1:11434/v1",
        "vllm": "http://127.0.0.1:8000/v1",
    }
    return OpenAICompatibleLlm(
        model=model,
        base_url=config.base_url or config.endpoint or defaults[config.type],
        api_key=credential,
    )
