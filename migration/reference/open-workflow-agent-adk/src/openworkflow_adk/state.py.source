"""Derivation of ADK workflow state schemas from workflow inputs and mappings."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ConfigDict, create_model

from openworkflow_adk.models import OpenWorkflowDocument, Task, TaskItem


def _names(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(re.findall(r"(?:\$context|\$workflow|\.)\.?([A-Za-z_][\w-]*)", value))
    if isinstance(value, dict):
        result = set(value)
        for item in value.values():
            result.update(_names(item))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(_names(item))
        return result
    return set()


def _object_literal_keys(value: Any) -> set[str]:
    """Statically extract object-literal keys from expression strings.

    ADK validates state mutations against the derived schema, so keys written
    by ``export.as`` (and similar object-building expressions such as
    ``$context + { error: $error }``) must be declared upfront. Only literal
    keys can be extracted; computed keys cannot be declared statically.
    """
    if not isinstance(value, str):
        return set()
    text = value.strip()
    if text.startswith("${") and text.endswith("}"):
        text = text[2:-1].strip()
    keys: set[str] = set()
    index = 0
    while index < len(text):
        if text[index] != "{":
            index += 1
            continue
        end = _balanced_end(text, index)
        if end is None:
            return keys
        keys.update(_entry_keys(text[index + 1 : end]))
        index = end + 1
    return keys


def _balanced_end(text: str, start: int) -> int | None:
    """Return the index of the brace closing ``text[start]``, quote-aware."""
    depth = 0
    quote: str | None = None
    index = start
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _entry_keys(body: str) -> set[str]:
    """Extract identifier keys from comma-separated ``key: value`` entries."""
    keys: set[str] = set()
    depth = 0
    quote: str | None = None
    segments: list[str] = []
    entry_start = 0
    index = 0
    while index < len(body):
        char = body[index]
        if quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == "," and depth == 0:
            segments.append(body[entry_start:index])
            entry_start = index + 1
        index += 1
    segments.append(body[entry_start:])
    for segment in segments:
        segment = segment.strip()
        colon = segment.find(":")
        if colon <= 0:
            continue
        key = segment[:colon].strip()
        if len(key) >= 2 and key[0] in {"'", '"', "`"} and key[-1] == key[0]:
            key = key[1:-1]
        if key.isidentifier():
            keys.add(key)
    return keys


def _task_keys(task: Task) -> set[str]:
    keys: set[str] = set()
    agent_config = task.effective_agent()
    if agent_config is not None and agent_config.output_key:
        keys.add(agent_config.output_key)
    if isinstance(task.set, dict):
        keys.update(task.set)
    if isinstance(task.for_, dict):
        for key in ("each", "at"):
            value = task.for_.get(key)
            if isinstance(value, str):
                keys.add(value)
    if isinstance(task.fork, dict):
        for branch in task.fork.get("branches", []):
            keys.update(_task_keys(TaskItem.model_validate(branch).task))
    if isinstance(task.switch, list):
        for case in task.switch:
            if isinstance(case, dict):
                for configuration in case.values():
                    if isinstance(configuration, dict):
                        keys.update(_names(configuration.get("when")))
    catch = task.catch
    if isinstance(catch, dict) and isinstance(catch.get("as"), str):
        keys.add(catch["as"])
    for child in [*(task.do or []), *(task.try_ or [])]:
        keys.update(_task_keys(child.task))
    if isinstance(catch, dict):
        for child in catch.get("do", []):
            keys.update(_task_keys(TaskItem.model_validate(child).task))
    foreach_config = None
    if isinstance(task.listen, dict):
        foreach_config = task.listen.get("foreach")
    if foreach_config is None:
        foreach_config = (task.model_extra or {}).get("foreach")
    if isinstance(foreach_config, dict):
        for key in ("item", "at"):
            if isinstance(foreach_config.get(key), str):
                keys.add(foreach_config[key])
        for child in foreach_config.get("do", []):
            keys.update(_task_keys(TaskItem.model_validate(child).task))
        foreach_export = foreach_config.get("export")
        if isinstance(foreach_export, dict):
            keys.update(_object_literal_keys(foreach_export.get("as")))
    keys.update(_names(task.if_))
    keys.update(_names(task.input))
    keys.update(_names(task.output))
    keys.update(_names(task.export))
    for mapping in (task.output, task.export):
        if isinstance(mapping, dict) and isinstance(mapping.get("as"), str):
            keys.update(_object_literal_keys(mapping["as"]))
    return keys


def _extension_keys(raw_extensions: list[Any]) -> set[str]:
    """Collect state keys written by ``use.extensions`` before/after tasks."""
    keys: set[str] = set()
    for entry in raw_extensions:
        extension: Any = entry
        if isinstance(entry, dict) and "extend" not in entry and len(entry) == 1:
            _, extension = next(iter(entry.items()))
        if not isinstance(extension, dict):
            continue
        for phase in ("before", "after"):
            for item in extension.get(phase) or []:
                keys.update(_task_keys(TaskItem.model_validate(item).task))
    return keys


def derive_state_schema(document: OpenWorkflowDocument) -> type:
    """Create an open Pydantic model containing workflow state keys."""
    keys = _names(document.input)
    if isinstance(document.output, dict) and isinstance(document.output.get("as"), str):
        keys.update(_object_literal_keys(document.output["as"]))
    for item in document.do:
        agent_config = item.task.effective_agent()
        if agent_config is not None:
            keys.add(agent_config.output_key or item.name)
        keys.update(_task_keys(item.task))
    for function in document.use.functions.values():
        keys.update(_task_keys(Task.model_validate(function)))
    keys.update(_extension_keys(document.use.extensions))
    fields = {key: (Any, None) for key in sorted(keys) if key.isidentifier()}
    return create_model(
        f"{document.document.name.title().replace('-', '')}State",
        __config__=ConfigDict(extra="allow"),
        **fields,
    )
