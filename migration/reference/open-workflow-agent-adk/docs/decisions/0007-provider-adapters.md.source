# ADR 0007: Direct `BaseLlm` provider adapters

## Status

Accepted

## Decision

Use small direct `google.adk.models.BaseLlm` adapters for the providers that are
not guaranteed to be installed with ADK: OpenAI-compatible APIs (including
Azure, Ollama, and vLLM), Anthropic Messages, and AWS Bedrock Converse.

The provider factory resolves named configuration and returns a validated
`BaseLlm` instance directly to the ADK `LlmAgent`. Gemini remains an ADK model
string. This is deliberately preferred over adding LiteLLM as a mandatory
runtime dependency.

ADK's `LLMRegistry` registers classes and constructs them from only a model
string; it cannot carry a workflow's resolved secret, endpoint, or provider
options. Therefore configured instances are injected at agent construction,
while native registry resolution remains available for ADK-managed providers.

## Consequences

- Credentials remain secret references resolved at runtime, never inline model
  configuration.
- Provider transport behavior is independently mockable and has no optional
  SDK import requirement beyond Bedrock's lazy `boto3` import.
- A future host that wants registry-only resolution can register a separate
  configured adapter class, but must own its lifecycle and secret scope.
