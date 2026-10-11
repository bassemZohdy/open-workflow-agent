"""Three-layer configuration resolution for task agent characteristics."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from openworkflow_adk.models import (
    AgentCharacteristics,
    MemoryConfig,
    ModelReference,
    ModelSpec,
    ProviderConfig,
)


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_defaults(
    source: Mapping[str, Any] | BaseModel | str | Path | None = None,
) -> dict[str, Any]:
    """Load project defaults from a mapping, Pydantic model, or YAML/JSON file."""
    if source is None:
        return {}
    if isinstance(source, BaseModel):
        return source.model_dump(exclude_none=True)
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source)
    value = (
        yaml.safe_load(path.read_text())
        if path.suffix.lower() in {".yaml", ".yml"}
        else json.loads(path.read_text())
    )
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("agent defaults must be an object")
    return value


def _parse_value(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def environment_config(
    environ: Mapping[str, str] | None = None, prefix: str = "WORKFLOW_"
) -> dict[str, Any]:
    """Convert `WORKFLOW_AGENT__MODEL` variables into nested configuration."""
    values = os.environ if environ is None else environ
    result: dict[str, Any] = {}
    for key, value in values.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix) :].lower().split("__")
        if not path or path[0] != "agent":
            continue
        target = result
        for part in path[:-1]:
            target = target.setdefault(part, {})
        target[path[-1]] = _parse_value(value)
    return result


def model_registry_config(environ: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """Read named model bundle overrides from ``WORKFLOW_MODELS__...`` variables."""
    values = os.environ if environ is None else environ
    result: dict[str, dict[str, Any]] = {}
    prefix = "WORKFLOW_MODELS__"
    for key, value in values.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix) :].lower().split("__")
        if len(path) < 2:
            continue
        target = result.setdefault(path[0], {})
        for part in path[1:-1]:
            target = target.setdefault(part, {})
        target[path[-1]] = _parse_value(value)
    return result


def named_registry_config(
    registry_name: str, environ: Mapping[str, str] | None = None
) -> dict[str, dict[str, Any]]:
    """Read a named registry's environment overrides."""
    values = os.environ if environ is None else environ
    prefix = f"WORKFLOW_{registry_name.upper()}__"
    result: dict[str, dict[str, Any]] = {}
    for key, value in values.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix) :].lower().split("__")
        if len(path) < 2:
            continue
        target = result.setdefault(path[0], {})
        for part in path[1:-1]:
            target = target.setdefault(part, {})
        target[path[-1]] = _parse_value(value)
    return result


def _reference_dict(reference: str | Mapping[str, Any] | BaseModel) -> dict[str, Any] | str:
    """Normalize a Pydantic reference model to a plain dict for resolution."""
    if isinstance(reference, BaseModel):
        return reference.model_dump(exclude_none=True)
    return reference


def resolve_provider_config(
    reference: str | Mapping[str, Any] | BaseModel,
    providers: Mapping[str, ProviderConfig] | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProviderConfig:
    """Resolve a named or inline provider with environment overrides."""
    reference = _reference_dict(reference)
    if isinstance(reference, Mapping) and "use" in reference:
        name = str(reference["use"])
        if name not in (providers or {}):
            raise ValueError(f"unknown provider reference {name!r}")
        values = (providers or {})[name].model_dump(exclude_none=True)
    elif isinstance(reference, str):
        name, values = reference, None
        if name not in (providers or {}):
            raise ValueError(f"unknown provider reference {name!r}")
        values = (providers or {})[name].model_dump(exclude_none=True)
    else:
        name, values = "inline", dict(reference)
    if name != "inline":
        values = _deep_merge(
            values or {}, named_registry_config("providers", environ).get(name, {})
        )
    return ProviderConfig.model_validate(values)


def resolve_memory_config(
    reference: str | Mapping[str, Any] | BaseModel,
    memories: Mapping[str, MemoryConfig] | None = None,
    environ: Mapping[str, str] | None = None,
) -> MemoryConfig:
    """Resolve a named or inline memory backend with environment overrides."""
    reference = _reference_dict(reference)
    if isinstance(reference, Mapping) and "use" in reference:
        name = str(reference["use"])
        if name not in (memories or {}):
            raise ValueError(f"unknown memory reference {name!r}")
        values = (memories or {})[name].model_dump(exclude_none=True)
    elif isinstance(reference, str):
        name = reference
        if name not in (memories or {}):
            raise ValueError(f"unknown memory reference {name!r}")
        values = (memories or {})[name].model_dump(exclude_none=True)
    else:
        name, values = "inline", dict(reference)
    if name != "inline":
        values = _deep_merge(values, named_registry_config("memories", environ).get(name, {}))
    return MemoryConfig.model_validate(values)


def resolve_model_spec(
    reference: ModelReference | str | None,
    models: Mapping[str, ModelSpec | dict[str, Any]] | None = None,
    environ: Mapping[str, str] | None = None,
) -> ModelSpec | None:
    """Resolve a named model reference while preserving literal model support."""
    if reference is None:
        return None
    if isinstance(reference, str):
        return ModelSpec(model=reference)
    registry = models or {}
    if reference.use not in registry:
        raise ValueError(f"unknown model reference {reference.use!r}")
    entry = registry[reference.use]
    named = entry.model_dump(exclude_none=True) if isinstance(entry, BaseModel) else dict(entry)
    overrides = model_registry_config(environ).get(reference.use, {})
    named = _deep_merge(named, overrides)
    return ModelSpec.model_validate(named)


def _coalesce_model(*candidates: Any) -> Any:
    """Return the first non-None model value from highest to lowest precedence."""
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


def resolve_agent_characteristics(
    task: AgentCharacteristics | Mapping[str, Any] | None = None,
    defaults: Mapping[str, Any] | BaseModel | str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    models: Mapping[str, ModelSpec | dict[str, Any]] | None = None,
    providers: Mapping[str, ProviderConfig] | None = None,
) -> AgentCharacteristics:
    """Resolve defaults, task configuration, and environment overrides.

    Precedence (highest wins): environment > task > document defaults > named model spec.
    When the final model is a named reference it is resolved to its spec so the agent
    builder receives a concrete model string (and inherited provider/generation config).
    """
    task_values = (
        task.model_dump(exclude_none=True)
        if isinstance(task, AgentCharacteristics)
        else dict(task or {})
    )
    default_values = load_defaults(defaults)
    env_values = environment_config(environ).get("agent", {})

    # Determine the winning model reference across layers.
    final_model = _coalesce_model(
        env_values.get("model"),
        task_values.get("model"),
        default_values.get("model"),
    )

    # Start from the resolved named model spec when applicable.
    if isinstance(final_model, Mapping) and "use" in final_model:
        spec = resolve_model_spec(ModelReference.model_validate(final_model), models, environ)
        merged = spec.model_dump(exclude_none=True)
    else:
        merged = {"model": final_model} if final_model is not None else {}

    # Apply non-model fields: defaults < task < env.
    merged = _deep_merge(merged, {k: v for k, v in default_values.items() if k != "model"})
    merged = _deep_merge(merged, {k: v for k, v in task_values.items() if k != "model"})
    merged = _deep_merge(merged, {k: v for k, v in env_values.items() if k != "model"})

    resolved = AgentCharacteristics.model_validate(merged)
    if resolved.provider:
        resolve_provider_config(resolved.provider.model_dump(), providers, environ)
    return resolved
