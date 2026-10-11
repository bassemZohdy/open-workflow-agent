"""Natural-language workflow generation with strict post-validation."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import yaml

from openworkflow_adk.loader import load
from openworkflow_adk.models import OpenWorkflowDocument
from openworkflow_adk.translator import build_workflow

GenerationResult = str | dict[str, Any]
Generator = Callable[[str], GenerationResult | Awaitable[GenerationResult]]


class WorkflowGenerationError(ValueError):
    """Raised when generated content is not a valid runnable workflow."""


GENERATION_INSTRUCTION = """Generate one OpenWorkflow DSL document for the user's request.
Return only a JSON object (no Markdown fences) with a document envelope and a
top-level do list. Use deterministic built-in tasks where possible. The result
will be validated and compiled before it is returned."""


async def generate_workflow(
    prompt: str,
    *,
    generator: Generator,
    namespace: str = "generated",
    name: str = "workflow",
    version: str = "1.0.0",
) -> OpenWorkflowDocument:
    """Generate, validate, and compile a runnable workflow document.

    ``generator`` is deliberately injectable so applications can connect any
    LLM/provider while tests and offline tooling remain deterministic.
    """
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    request = f"{GENERATION_INSTRUCTION}\n\nUser request:\n{prompt}"
    result = generator(request)
    if hasattr(result, "__await__"):
        result = await result
    raw: Any
    if isinstance(result, dict):
        raw = result
    elif isinstance(result, str):
        text = result.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            try:
                raw = yaml.safe_load(text)
            except yaml.YAMLError as error:
                raise WorkflowGenerationError("generator did not return JSON or YAML") from error
    else:
        raise WorkflowGenerationError("generator must return a mapping or JSON/YAML text")
    if not isinstance(raw, dict):
        raise WorkflowGenerationError("generated workflow must be an object")
    raw.setdefault(
        "document",
        {"dsl": "1.0.3", "namespace": namespace, "name": name, "version": version},
    )
    try:
        document = load(raw)
        build_workflow(document)
    except Exception as error:
        raise WorkflowGenerationError(f"generated workflow is not runnable: {error}") from error
    return document
